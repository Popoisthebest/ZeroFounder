import { describe, expect, it } from 'vitest'

import { pagesBase } from '../../../config/pages'

describe('GitHub Pages base path', () => {
  it('uses repository name for project pages', () => {
    expect(pagesBase('owner/ZeroFounder')).toBe('/ZeroFounder/')
  })

  it('uses root for user and organization pages', () => {
    expect(pagesBase('owner/owner.github.io')).toBe('/')
  })
})
