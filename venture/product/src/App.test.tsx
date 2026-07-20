import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import App from './App'

describe('unselected venture shell', () => {
  it('states that no product has been selected', () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: '아직 제품을 정하지 않았습니다.' })).toBeInTheDocument()
    expect(screen.getByText('개인정보와 방문 분석을 기본 수집하지 않습니다.')).toBeInTheDocument()
  })

  it('provides working feedback paths', () => {
    render(<App />)
    expect(screen.getByRole('link', { name: '버그 신고' })).toHaveAttribute('href', expect.stringContaining('bug-report.yml'))
    expect(screen.getByRole('link', { name: '기능 요청' })).toHaveAttribute('href', expect.stringContaining('feature-request.yml'))
  })
})
