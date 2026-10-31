import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect, test } from 'vitest'
import { ScenarioControls } from './ScenarioControls'
import type { DemoScenario } from '../types'

const scenarios: DemoScenario[] = [
  {id: 'rpm_spike', name: 'RPM spike', description: 'A spike followed by recovery.',
    duration_ns: 240e6, profile_id: 'democar-network', seed: 42, source_sha256: 'a'.repeat(64)},
  {id: 'abs_missing_frames', name: 'ABS missing frames', description: 'Silent ABS during motion.',
    duration_ns: 600e6, profile_id: 'democar-network', seed: 42, source_sha256: 'b'.repeat(64)},
]

test('scenario choice submits its ID and seed; disabled runs cannot replace an active recording', async () => {
  let chosen: [string, number] | undefined
  const {rerender} = render(<ScenarioControls scenarios={scenarios} view={null} disabled={false}
    onRun={(id, seed) => {chosen = [id, seed]}} />)
  const user = userEvent.setup()
  await user.selectOptions(screen.getByRole('combobox', {name: 'Scenario'}), 'abs_missing_frames')
  const seed = screen.getByRole('spinbutton', {name: 'Scenario seed'})
  await user.clear(seed)
  await user.type(seed, '7')
  await user.click(screen.getByRole('button', {name: 'Run scenario'}))
  expect(chosen).toEqual(['abs_missing_frames', 7])
  rerender(<ScenarioControls scenarios={scenarios} view={null} disabled onRun={() => {throw Error('replacement')}} />)
  expect(screen.getByRole('button', {name: 'Run scenario'})).toBeDisabled()
})
