export function pagesBase(repository = process.env.GITHUB_REPOSITORY ?? ''): string {
  const name = repository.split('/').at(-1) ?? ''
  if (!name || name.toLowerCase().endsWith('.github.io')) return '/'
  return `/${name}/`
}
