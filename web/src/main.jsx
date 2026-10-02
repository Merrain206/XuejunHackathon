import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import App from './App.jsx'
import { deckCss } from './shared/deckCss.js'
import './app.css'

// The deck stylesheet is injected from the shared module so the live deck and
// the exported offline HTML use byte-identical CSS.
const style = document.createElement('style')
style.id = 'deck-css'
style.textContent = deckCss()
document.head.appendChild(style)

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
