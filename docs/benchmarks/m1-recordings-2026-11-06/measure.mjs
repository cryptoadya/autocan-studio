import assert from 'node:assert/strict'
import {execFileSync, spawn} from 'node:child_process'
import {once} from 'node:events'
import {closeSync, cpSync, mkdirSync, openSync, readdirSync, readFileSync, renameSync, statSync, symlinkSync, writeFileSync} from 'node:fs'
import {resolve} from 'node:path'
import {setTimeout as delay} from 'node:timers/promises'
import {pathToFileURL} from 'node:url'

const [repo, scratch, fixture, modelRoot] = process.argv.slice(2).map(path => resolve(path))
assert(modelRoot, 'Usage: node measure.mjs REPO FRESH_DIRECTORY TRACE MODEL_ROOT')
const {chromium} = await import(pathToFileURL(repo + '/frontend/node_modules/playwright/index.mjs'))
const autocan = repo + '/.venv/bin/autocan'
const port = '18767'
const origin = 'http://127.0.0.1:' + port
const root = scratch + '/data/runs'
const archive = scratch + '/archive'
const manifest = JSON.parse(readFileSync(fixture + '/manifest.json'))
const duration = 120_000_000_000
assert.equal(manifest.status, 'complete')
assert.equal(manifest.end_time_ns, duration)
assert.equal(manifest.configuration.seed, 42)
assert.equal(manifest.live_model.model_id, 'random_forest')
mkdirSync(scratch)
symlinkSync(repo + '/frontend', scratch + '/frontend', 'dir')
mkdirSync(root, {recursive: true})
mkdirSync(archive)
const fixtureIds = Array.from({length: 63}, (_, i) => i.toString(16).padStart(32, '0'))
for (const id of fixtureIds) cpSync(fixture, root + '/' + id, {recursive: true, errorOnExist: true})
const filesBytes = path => readdirSync(path).reduce((sum, file) => sum + statSync(path + '/' + file).size, 0)
const fixtureBytes = filesBytes(fixture)
const rounded = ms => Number(ms.toFixed(3))

async function withStudio(operation) {
  const log = openSync(scratch + '/studio.log', 'a')
  const server = spawn(autocan, ['studio', '--port', port, '--trusted-model',
    ...['experiment', 'prepared', 'source'].map(name => modelRoot + '/' + name)], {
    cwd: scratch, stdio: ['ignore', log, log],
  })
  const exited = once(server, 'exit')
  try {
    for (let i = 0; ; i++) {
      assert(server.exitCode === null && i < 90, 'Studio startup failed; inspect studio.log')
      if (await fetch(origin + '/api/health').then(r => r.ok).catch(() => false)) break
      await delay(1000)
    }
    const rss = () => Number(execFileSync('ps', ['-o', 'rss=', '-p', String(server.pid)], {encoding: 'utf8'}).trim())
    return await operation(rss)
  } finally {
    server.kill('SIGINT')
    await exited
    closeSync(log)
  }
}

async function json(path, method = 'GET', data, status = 200) {
  const response = await fetch(origin + path, {
    method, headers: data === undefined ? {} : {'Content-Type': 'application/json'},
    body: data === undefined ? undefined : JSON.stringify(data),
  })
  const body = await response.json()
  assert.equal(response.status, status, JSON.stringify(body).slice(0, 500))
  return body
}

async function listInBrowser(browser, expected) {
  const page = await browser.newPage({viewport: {width: 1440, height: 900}})
  try {
    const response = page.waitForResponse(r => r.url() === origin + '/api/runs', {timeout: 240_000})
    const start = performance.now()
    await page.goto(origin, {waitUntil: 'domcontentloaded'})
    const result = await response
    assert.equal(result.status(), 200)
    const list = await result.json()
    assert.deepEqual(list, expected.map(id => ({id, duration_ns: duration})))
    const select = page.getByLabel('Saved recording')
    await page.waitForFunction(count => document.querySelector('[aria-label="Saved recording"]').options.length === count + 1, expected.length)
    assert.equal(await select.locator('option').count(), expected.length + 1)
    const uiMs = rounded(performance.now() - start)
    const timing = await page.evaluate(url => {
      const entry = performance.getEntriesByName(url, 'resource').at(-1)
      return {fetchMs: entry.duration, ttfbMs: entry.responseStart - entry.requestStart, bytes: entry.decodedBodySize}
    }, origin + '/api/runs')
    assert.equal(await page.getByRole('alert').count(), 0)
    return {count: list.length, uiMs, fetchMs: rounded(timing.fetchMs), ttfbMs: rounded(timing.ttfbMs), bytes: timing.bytes}
  } finally {
    await page.close()
  }
}

const spec = {seed: 42, profile_id: 'democar-network'}
async function record(rss, target) {
  const start = performance.now()
  const {id} = await json('/api/simulations', 'POST', spec)
  const sim = '/api/simulations/' + id
  await json(sim + '/start', 'POST')
  for (let i = 0; ; i++) {
    assert(i < 100, 'Recording did not reach target')
    const state = await json(sim + '/state')
    assert.equal(state.recording, 'recording')
    if (state.sim_time_ns >= target) break
    await delay(100)
  }
  const stopped = await json(sim + '/stop', 'POST')
  assert.equal(stopped.recording, 'saved')
  const wallMs = rounded(performance.now() - start)
  const playback = '/api/runs/' + id + '/playback'
  const replay = await json(playback)
  assert.equal(replay.sim_time_ns, stopped.sim_time_ns)
  assert.deepEqual(replay.anomaly, stopped.anomaly)
  assert.deepEqual(replay.anomaly.model, manifest.live_model)
  const earlier = await json(playback + '?until_ns=500000000')
  assert.equal(earlier.sim_time_ns, 500_000_000)
  assert(earlier.anomaly.scores.length > 0)
  assert(earlier.anomaly.scores.every(score => score.sim_time_ns <= earlier.sim_time_ns))
  return {id, endNs: stopped.sim_time_ns, wallMs, scoreSamples: replay.anomaly.scores.length, rssKiB: rss()}
}

const browser = await chromium.launch({channel: 'chrome', chromiumSandbox: true})
try {
  const crowded = await withStudio(async rss => {
    console.log('Measuring the 63-recording browser load')
    const rssBeforeKiB = rss()
    const list = await listInBrowser(browser, fixtureIds)
    const rssAfterListKiB = rss()
    const last = await record(rss, 1_000_000_000)
    assert.equal(readdirSync(root).length, 64)
    const rejection = await json('/api/simulations', 'POST', spec, 409)
    assert.match(rejection.detail, /run quota reached \(64\)/)
    assert.equal(readdirSync(root).length, 64)
    return {list, rssBeforeKiB, rssAfterListKiB, last, rejection: {status: 409, detail: rejection.detail}}
  })
  console.log(JSON.stringify(crowded))

  const keep = fixtureIds[0]
  const archivedIds = readdirSync(root).filter(id => id !== keep)
  const archivedBytes = archivedIds.reduce((total, id) => total + filesBytes(root + '/' + id), 0)
  const archiveStart = performance.now()
  for (const id of archivedIds) renameSync(root + '/' + id, archive + '/' + id)
  const archiveMs = rounded(performance.now() - archiveStart)
  assert.equal(readdirSync(archive).length, 63)
  assert.deepEqual(readdirSync(root), [keep])
  execFileSync(autocan, ['playback', archive + '/' + fixtureIds[1]], {stdio: 'ignore'})

  const recovered = await withStudio(async rss => {
    const list = await listInBrowser(browser, [keep])
    const rssBeforeCyclesKiB = rss()
    const cycles = []
    for (let i = 0; i < 10; i++) cycles.push(await record(rss, 2_000_000_000))
    const listed = await json('/api/runs')
    assert.deepEqual(listed.map(run => run.id).sort(), [keep, ...cycles.map(cycle => cycle.id)].sort())
    await delay(1000)
    return {list, rssBeforeCyclesKiB, cycles, finalCount: listed.length, rssAfterCyclesKiB: rss()}
  })
  const result = {
    revision: execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD'], {encoding: 'utf8'}).trim(),
    browser: browser.version(), seed: 42, fixture: {bytes: fixtureBytes, endNs: duration, digest: manifest.semantic_sha256, model: manifest.live_model},
    crowded, cleanup: {moved: archivedIds.length, archivedBytes, archiveMs}, recovered,
  }
  writeFileSync(scratch + '/measurement.json', JSON.stringify(result, null, 2) + '\n')
  console.log(JSON.stringify(result))
} finally {
  await browser.close()
}
