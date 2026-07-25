import { defineConfig } from '@hey-api/openapi-ts'

export default defineConfig({
  input: '../docx/deliverables/openapi.yaml',
  output: {
    path: 'src/api/generated',
    format: 'prettier',
  },
  plugins: ['@hey-api/client-fetch', '@hey-api/typescript', '@hey-api/sdk'],
})
