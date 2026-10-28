import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { initTheme } from './lib/palette'
import { ui } from './lib/ui'

initTheme()
ui.applyFont()
ui.setAnimLevel(ui.get().animLevel)

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
