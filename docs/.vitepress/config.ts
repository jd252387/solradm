import { defineConfig } from 'vitepress'
import { withSidebar } from 'vitepress-sidebar'

const config = {
  title: 'solradm',
  description: 'Solr Administration CLI',
  themeConfig: {
    nav: [
      { text: 'Context System', link: '/context-system' },
      { text: 'Commands', link: '/commands' }
    ],
    search: {
      provider: 'local',
      options: {
        detailed: true,
        fullTextSearch: true
      }
    }
  }
}

export default defineConfig(
  withSidebar(config, {
    documentRootPath: './docs'
  })
)
