import { expect, type Locator } from '@playwright/test'

// Native range inputs do not support fill(). Respect actionability before
// sending input/release through React's normal handlers.
export async function setRange(range: Locator, value: number) {
  await expect(range).toBeEnabled()
  await range.evaluate((element, next) => {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
    setter.call(element, String(next))
    element.dispatchEvent(new Event('input', {bubbles: true}))
  }, value)
  await range.dispatchEvent('pointerup')
}
