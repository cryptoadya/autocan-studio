import { expect, test, type Locator } from '@playwright/test'

// Native range inputs do not support fill(). Send input and release through the
// rendered control, preserving React's normal command/seek handlers.
async function setRange(range: Locator, value: number) {
  // Synthetic input/release bypasses browser actionability. Wait until a newly
  // created run has finished connecting before sending commands through it.
  await expect(range).toBeEnabled()
  await range.evaluate((element, next) => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setter.call(element, String(next))
    element.dispatchEvent(new Event('input', {bubbles: true}))
  }, value)
  await range.dispatchEvent('pointerup')
}

test('throttle → CAN → KOMBI → silent bus → save → playback → rerun', async ({page}, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/')
  await page.getByRole('button', {name: 'Run demo', exact: true}).click()
  await expect(page.getByText('Connected locally', {exact: true})).toBeVisible()
  await expect(page.locator('.measurement strong')).toHaveText('FRESH')
  await setRange(page.getByRole('slider', {name: 'Throttle', exact: true}), 50)
  await expect.poll(async () => Number(
    (await page.getByTestId('received-rpm').innerText()).replace(/[^0-9]/g, ''),
  )).toBeGreaterThan(800)
  await expect(page.locator('tbody tr').first()).toContainText('0x200')

  await page.getByRole('button', {name: 'Take Engine ECU offline'}).click()
  await expect(page.locator('.measurement strong')).toHaveText('TIMEOUT')
  const offline = await (await page.request.get('/api/simulations/current')).json()
  expect(offline.state.engine_running).toBe(true)
  expect(offline.state.engine_publishing).toBe(false)
  expect(offline.state).not.toHaveProperty('physical_rpm')
  // A new physical input while offline must not refresh the held CAN measurement.
  await setRange(page.getByRole('slider', {name: 'Throttle', exact: true}), 0)
  await expect.poll(async () => {
    const current = await (await page.request.get('/api/simulations/current')).json()
    return current.sim_time_ns
  }).toBeGreaterThanOrEqual(offline.sim_time_ns + 300_000_000)
  await page.getByRole('button', {name: 'Pause', exact: true}).click()
  await expect(page.getByRole('button', {name: 'Resume', exact: true})).toBeEnabled()
  const paused = await (await page.request.get('/api/simulations/current')).json()
  expect(paused.state.kombi_rpm).toBe(offline.state.kombi_rpm)
  expect(paused.state.frames_published).toBe(offline.state.frames_published)
  expect(paused.state.engine_running).toBe(true)
  expect(paused.state.throttle_percent).toBe(0)
  expect(paused.sim_time_ns).toBeGreaterThan(offline.sim_time_ns)

  await page.reload()
  await expect(page.getByText('Connected locally', {exact: true})).toBeVisible()
  await expect(page.getByRole('button', {name: 'Resume', exact: true})).toBeEnabled()
  const reconnected = await (await page.request.get('/api/simulations/current')).json()
  expect(reconnected.id).toBe(paused.id)
  expect(reconnected.sim_time_ns).toBe(paused.sim_time_ns)
  await expect(page.locator('.measurement strong')).toHaveText('TIMEOUT')

  await page.getByRole('button', {name: 'Stop & save', exact: true}).click()
  await expect(page.getByLabel('Saved recording').locator(`option[value="${paused.id}"]`)).toHaveCount(1)
  await page.getByLabel('Saved recording').selectOption(paused.id)
  await page.getByRole('button', {name: 'Load playback', exact: true}).click()
  await expect(page.locator('.mode')).toHaveText('PLAYBACK')
  await expect(page.getByRole('slider', {name: 'Throttle', exact: true})).toBeDisabled()
  await expect(page.getByRole('button', {name: 'Bring Engine ECU online'})).toBeDisabled()
  await expect(page.locator('.measurement strong')).toHaveText('TIMEOUT')

  // Saved virtual time makes the short STALE interval and exact deadlines
  // testable independently of browser/wall-clock scheduling.
  for (const [delta, status] of [[50_000_000, 'FRESH'], [60_000_000, 'STALE'], [200_000_000, 'TIMEOUT']] as const) {
    const target = offline.state.last_received_ns + delta
    const response = page.waitForResponse(r => r.url().endsWith(`/playback?until_ns=${target}`))
    await setRange(page.getByRole('slider', {name: 'Playback position'}), target)
    expect((await response).ok()).toBe(true)
    await expect(page.locator('.measurement strong')).toHaveText(status)
  }
  await page.getByRole('button', {name: 'Deterministic rerun', exact: true}).click()
  await expect(page.getByRole('status').filter({hasText: 'Deterministic rerun matched'})).toBeVisible()
  await page.screenshot({path: testInfo.outputPath('playback-rerun.png'), fullPage: true})
  await page.getByRole('button', {name: 'Return to live', exact: true}).click()
  await expect(page.locator('.mode')).toHaveText('LIVE')
  await page.getByRole('button', {name: 'Reset · new recording', exact: true}).click()
  await expect(page.getByTestId('received-rpm')).toHaveText('—rpm')
  await expect(page.locator('.measurement strong')).toHaveText('UNAVAILABLE')
  await page.setViewportSize({width: 390, height: 844})
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(errors).toEqual([])
})
