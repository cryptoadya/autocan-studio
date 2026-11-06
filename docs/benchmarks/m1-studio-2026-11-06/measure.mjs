import assert from 'node:assert/strict'
import {spawn, execFileSync} from 'node:child_process'
import {createWriteStream, readFileSync, readdirSync, statSync, writeFileSync} from 'node:fs'
import {setTimeout as delay} from 'node:timers/promises'
import {pathToFileURL} from 'node:url'

const [repo, scratch, modelRoot, mode] = process.argv.slice(2)
assert(mode === undefined || mode === '--auto-save', 'Expected --auto-save or no mode')
const autoSave = mode === '--auto-save'
const {chromium} = await import(pathToFileURL(repo + '/frontend/node_modules/playwright/index.mjs'))
const inputs = modelRoot ?? repo + '/data/recovered/m9-pr17-pytest272'
const origin = 'http://127.0.0.1:18765'
const server = spawn(repo + '/.venv/bin/autocan', [
  'studio', '--port', '18765', '--trusted-model', ...['experiment', 'prepared', 'source'].map(p => inputs + '/' + p),
], {cwd: scratch, stdio: ['ignore', 'pipe', 'pipe']})
const log = createWriteStream(scratch + '/studio.log')
server.stdout.pipe(log, {end: false})
server.stderr.pipe(log, {end: false})
const clock = () => performance.now()
const round = n => Number(n.toFixed(3))
let browser, interval, phase = 'startup'
try {
  for (let i = 0; ; i++) {
    assert(server.exitCode === null && i < 180, 'Studio failed to start; inspect studio.log')
    if (await fetch(origin + '/api/health').then(r => r.ok).catch(() => false)) break
    await delay(1000)
  }
  browser = await chromium.launch({channel: 'chrome', chromiumSandbox: true})
  const page = await browser.newPage({viewport: {width: 1440, height: 900}})
  const errors = [], rss = [], frames = []
  let lastMessage, terminal, runStart, ended
  const sampleRss = () => {
    const fields = execFileSync('ps', ['-o', 'rss=', '-p', String(server.pid)], {encoding: 'utf8'})
    rss.push({phase, seconds: runStart === undefined ? null : round((clock() - runStart) / 1000), kib: Number(fields.trim())})
  }
  page.on('pageerror', e => errors.push(e.message))
  page.on('console', m => {if (m.type() === 'error') errors.push(m.text())})
  page.on('websocket', socket => {
    socket.on('framereceived', ({payload}) => {
      if (terminal) return
      const message = JSON.parse(payload)
      const now = clock()
      frames.push({gap: lastMessage === undefined ? null : now - lastMessage, bytes: Buffer.byteLength(payload), skipped: message.skipped_frames})
      lastMessage = now
      if (message.stopped) {terminal = message; ended = now}
    })
  })
  await page.goto(origin)
  sampleRss()
  phase = 'live'
  interval = setInterval(sampleRss, 5000)
  const created = page.waitForResponse(r => r.url() === origin + '/api/simulations' && r.request().method() === 'POST')
  runStart = clock()
  await page.getByRole('button', {name: 'Run demo', exact: true}).click()
  const initial = await (await created).json()
  console.log('Recording', initial.id)
  await page.getByText('Scoring active', {exact: true}).waitFor()
  if (!autoSave) {
    await page.waitForFunction(() => Number.parseFloat(document.querySelector('.sim-time').textContent) >= 77,
      undefined, {timeout: 240_000})
    await page.getByRole('button', {name: 'Stop & save', exact: true}).click()
  }
  await page.locator('footer').getByText(/^Saved ·|^Recording failed ·/).waitFor({timeout: 360_000})
  const endSeconds = terminal.sim_time_ns / 1e9
  assert.equal(terminal.recording, 'saved', terminal.error)
  if (autoSave) assert.equal(endSeconds, 120)
  else assert(endSeconds >= 77 && endSeconds < 78)
  sampleRss()
  phase = 'playback'
  const saved = page.getByLabel('Saved recording')
  await saved.selectOption(initial.id, {timeout: 30000})
  const current = await (await page.request.get(origin + '/api/simulations/current')).json()
  const playback = []
  const score = page.getByTestId('anomaly-score')
  for (let trial = 1; trial <= 5; trial++) {
    for (const target of [endSeconds, 60, autoSave ? 119 : 76]) {
      const action = target === endSeconds
        ? page.getByRole('button', {name: 'Load playback', exact: true})
        : page.getByRole('slider', {name: 'Playback position'})
      await action.scrollIntoViewIfNeeded()
      const url = origin + '/api/runs/' + initial.id + '/playback' + (target === endSeconds ? '' : '?until_ns=' + target * 1e9)
      const response = page.waitForResponse(r => r.url() === url)
      const start = clock()
      if (target === endSeconds) await action.click()
      else {
        await action.evaluate((range, value) => {
          const setValue = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set
          setValue.call(range, String(value))
          range.dispatchEvent(new Event('input', {bubbles: true}))
        }, target * 1e9)
        await action.dispatchEvent('pointerup')
      }
      const result = await response
      assert.equal(result.status(), 200)
      const body = await result.json()
      assert.equal(body.sim_time_ns, Math.round(target * 1e9))
      assert.deepEqual(body.anomaly.model, current.anomaly.model)
      assert(body.anomaly.scores.every(s => s.sim_time_ns <= body.sim_time_ns))
      if (target === endSeconds) assert.deepEqual(body.anomaly, current.anomaly)
      await page.getByText('Recorded score', {exact: true}).waitFor()
      await page.waitForFunction(time => document.querySelector('.sim-time')?.textContent.startsWith(time), target.toFixed(2))
      assert.equal(await score.textContent(), body.anomaly.scores.at(-1).score.toFixed(3))
      const uiMs = round(clock() - start)
      const timing = await page.evaluate(url => {
        const t = performance.getEntriesByName(url, 'resource').at(-1)
        return {fetchMs: t.duration, ttfbMs: t.responseStart - t.requestStart, bodyBytes: t.decodedBodySize}
      }, url)
      playback.push({trial, target, uiMs, fetchMs: round(timing.fetchMs), ttfbMs: round(timing.ttfbMs), bodyBytes: timing.bodyBytes,
        frames: body.frames.length, signals: body.signals.length, scores: body.anomaly.scores.length})
      sampleRss()
    }
  }
  assert.equal(await page.getByRole('alert').count(), 0)
  assert.deepEqual(errors, [])
  const runPath = scratch + '/data/runs/' + initial.id
  const manifest = JSON.parse(readFileSync(runPath + '/manifest.json', 'utf8'))
  const files = Object.fromEntries(readdirSync(runPath).map(name => [name, statSync(runPath + '/' + name).size]))
  const stats = numbers => {
    const sorted = [...numbers].sort((a, b) => a - b)
    return Object.fromEntries([['min', 0], ['p50', .5], ['p95', .95], ['max', 1]].map(([label, q]) =>
      [label, round(sorted[Math.max(0, Math.ceil(q * sorted.length) - 1)])]))
  }
  const {status, end_time_ns, semantic_sha256, artifacts, live_model} = manifest
  assert.equal(status, 'complete')
  assert.equal(end_time_ns, terminal.sim_time_ns)
  const trace = {status, end_time_ns, semantic_sha256, artifacts, live_model}
  const result = {revision: execFileSync('git', ['-C', repo, 'rev-parse', 'HEAD'], {encoding: 'utf8'}).trim(), browser: browser.version(), seed: 42, profile: 'democar-network', live: {
    wallSeconds: round((ended - runStart) / 1000), stopTargetNs: autoSave ? 120e9 : 77e9, messages: frames.length,
    receiveGapMs: stats(frames.slice(1).map(f => f.gap)), payloadBytes: stats(frames.map(f => f.bytes)),
    skippedFrames: frames.reduce((total, frame) => total + frame.skipped, 0),
    retained: {frames: current.frames.length, signals: current.signals.length, scores: current.anomaly.scores.length, alerts: current.anomaly.alerts.length},
    lastScore: current.anomaly.scores.at(-1)
  }, trace, files, rss, playback, browserErrors: errors}
  writeFileSync(scratch + '/measurement.json', JSON.stringify(result, null, 2) + '\n')
  console.log(JSON.stringify({live: result.live, files, playback}))
} finally {
  clearInterval(interval)
  await browser?.close()
  server.kill('SIGINT')
}
