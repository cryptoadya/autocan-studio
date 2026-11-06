import { expect, test } from '@playwright/test'
import type { Snapshot } from '../src/types'
import { setRange } from './controls'

test('trusted live scores, alert and injected fault survive saved playback', async ({page}, testInfo) => {
  test.skip(!process.env.AUTOCAN_E2E_MODEL_ROOT, 'Requires a trusted local M9 experiment bundle')
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => {if (message.type() === 'error') errors.push(message.text())})

  // Prepare a moving DemoCar. The frozen model can already alert on a parked idle run.
  const created = await page.request.post('/api/simulations', {data: {seed: 42, profile_id: 'democar-network'}})
  await expect(created).toBeOK()
  const {id} = await created.json() as Snapshot
  const simulation = `/api/simulations/${id}`
  const snapshot = async (): Promise<Snapshot> => (await page.request.get('/api/simulations/current')).json()
  await expect(await page.request.patch(`${simulation}/inputs`, {
    data: {ignition: 'START', brake: 100, gear: 'D'},
  })).toBeOK()
  await expect(await page.request.post(`${simulation}/start`)).toBeOK()
  await expect.poll(async () => (await snapshot()).state.gear, {intervals: [20]}).toBe('D')
  await expect(await page.request.post(`${simulation}/pause`)).toBeOK()
  await expect(await page.request.patch(`${simulation}/inputs`, {data: {throttle: 50, brake: 0}})).toBeOK()

  await page.goto('/')
  await expect(page).toHaveTitle(/AutoCAN Studio/)
  await page.getByRole('button', {name: 'Resume', exact: true}).click()
  const panel = page.getByRole('region', {name: 'Anomaly detection'})
  await expect(panel.getByText('Scoring active')).toBeVisible()
  await expect(panel.getByText(/Model random_forest/)).toBeVisible()
  await expect(panel.getByTestId('anomaly-episode')).toHaveCount(0)
  const baseline = (await snapshot()).anomaly!
  expect(baseline.alerts).toHaveLength(0)
  const beforeFault = baseline.scores.at(-1)!
  expect(beforeFault.eligible).toBe(true)
  expect(beforeFault.score).toBeLessThan(baseline.model.threshold)

  await page.getByRole('button', {name: 'Stop ABS/DSC publication', exact: true}).click()
  await expect(panel.getByTestId('ground-truth-fault')).toBeVisible()
  await expect(panel.getByTestId('anomaly-episode')).toBeVisible()
  await page.getByRole('button', {name: 'Stop & save', exact: true}).click()
  const current = await snapshot()
  expect(current.stopped).toBe(true)
  const absFault = current.timeline?.find(event =>
    event.event_kind === 'FaultActivated' && event.fault_id?.startsWith('abs-offline-'))
  expect(absFault).toBeDefined()
  expect(absFault!.sim_time_ns).toBeGreaterThan(beforeFault.sim_time_ns)
  const anomaly = current.anomaly!
  expect(anomaly.alerts).toHaveLength(1)
  expect(anomaly.alerts[0].start_ns).toBeGreaterThan(absFault!.sim_time_ns)
  const score = anomaly.scores.at(-1)!.score.toFixed(3)
  const threshold = anomaly.model.threshold.toFixed(3)
  await expect(panel.getByTestId('anomaly-score')).toHaveText(score)
  await expect(panel.getByTestId('anomaly-threshold')).toHaveText(threshold)
  await panel.screenshot({path: testInfo.outputPath('anomaly-live.png')})

  await page.getByLabel('Saved recording').selectOption(current.id)
  await page.getByRole('button', {name: 'Load playback', exact: true}).click()
  await expect(panel.getByText('Recorded score')).toBeVisible()
  await expect(panel.getByTestId('anomaly-score')).toHaveText(score)
  await expect(panel.getByTestId('anomaly-threshold')).toHaveText(threshold)
  await expect(panel.getByTestId('anomaly-episode')).toBeVisible()
  await expect(panel.getByTestId('ground-truth-fault')).toBeVisible()
  await page.setViewportSize({width: 390, height: 844})
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390)
  await panel.screenshot({path: testInfo.outputPath('anomaly-playback-mobile.png')})
  await setRange(page.getByRole('slider', {name: 'Playback position'}), beforeFault.sim_time_ns)
  await expect(panel.getByTestId('anomaly-score')).toHaveText(beforeFault.score.toFixed(3))
  await expect(panel.getByTestId('anomaly-episode')).toHaveCount(0)
  await expect(panel.getByTestId('ground-truth-fault')).toHaveCount(0)
  await expect(page.getByRole('alert')).toHaveCount(0)
  expect(errors).toEqual([])
})
