import { expect, test } from '@playwright/test'

test('physical Stop/Start and ignition stay independent of publication Offline', async ({page}, testInfo) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('/')
  const previous = await (await page.request.get('/api/simulations/current')).json()
  if (previous) {
    await page.getByRole('button', {name: 'Reset · new recording', exact: true}).click()
    await page.getByRole('button', {name: 'Resume', exact: true}).click()
  } else {
    await page.getByRole('button', {name: 'Run demo', exact: true}).click()
  }
  await expect(page.getByText('Physical engine: Running', {exact: true})).toBeVisible()
  await expect(page.locator('.measurement strong')).toHaveText('FRESH')

  await page.getByRole('combobox', {name: 'Ignition', exact: true}).selectOption('OFF')
  await expect(page.getByText('Physical engine: Stopped', {exact: true})).toBeVisible()
  await expect(page.getByTestId('received-rpm')).toHaveText('0rpm')
  await expect(page.locator('.measurement strong')).toHaveText('FRESH')
  await expect(page.locator('tbody tr').first()).toContainText('00 00')
  await expect(page.getByRole('button', {name: 'Take Engine ECU offline'})).toBeEnabled()

  await page.getByRole('combobox', {name: 'Ignition', exact: true}).selectOption('ON')
  await expect.poll(async () => (await (await page.request.get('/api/simulations/current')).json()).state.ignition_state).toBe('ON')
  await expect(page.getByText('Physical engine: Stopped', {exact: true})).toBeVisible()
  await page.getByRole('button', {name: 'Take Engine ECU offline'}).click()
  await expect(page.locator('.measurement strong')).toHaveText('TIMEOUT')
  await page.getByRole('button', {name: 'Start engine', exact: true}).click()
  await expect(page.getByText('Physical engine: Running', {exact: true})).toBeVisible()
  await expect(page.getByTestId('received-rpm')).toHaveText('0rpm')
  await expect(page.locator('.measurement strong')).toHaveText('TIMEOUT')

  await page.getByRole('button', {name: 'Bring Engine ECU online'}).click()
  await expect(page.locator('.measurement strong')).toHaveText('FRESH')
  await expect.poll(async () => Number(
    (await page.getByTestId('received-rpm').innerText()).replace(/[^0-9]/g, ''),
  )).toBeGreaterThan(0)
  await page.getByRole('button', {name: 'Stop engine', exact: true}).click()
  await expect(page.getByTestId('received-rpm')).toHaveText('0rpm')
  await expect(page.locator('.measurement strong')).toHaveText('FRESH')
  await page.screenshot({path: testInfo.outputPath('engine-controls.png'), fullPage: true})
  const savedId = (await (await page.request.get('/api/simulations/current')).json()).id
  await page.getByRole('button', {name: 'Stop & save', exact: true}).click()
  await expect(page.getByLabel('Saved recording').locator(`option[value="${savedId}"]`)).toHaveCount(1)
  await page.getByLabel('Saved recording').selectOption(savedId)
  await page.getByRole('button', {name: 'Load playback', exact: true}).click()
  await expect(page.locator('.mode')).toHaveText('PLAYBACK')
  await expect(page.getByText('Physical engine: Stopped', {exact: true})).toBeVisible()
  await expect(page.getByRole('combobox', {name: 'Ignition', exact: true})).toBeDisabled()
  await expect(page.getByRole('button', {name: 'Start engine', exact: true})).toBeDisabled()
  await page.getByRole('button', {name: 'Deterministic rerun', exact: true}).click()
  await expect(page.getByRole('status').filter({hasText: 'Deterministic rerun matched'})).toBeVisible()
  await page.setViewportSize({width: 390, height: 844})
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(errors).toEqual([])
})
