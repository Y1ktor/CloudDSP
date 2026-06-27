/**
 * main.jsx
 * This is the root entry point for the entire React application. 
 * Vite (our build tool) looks for this file to know how to start the app.
 * 
 * Usage:
 * It imports the core React libraries, grabs the raw HTML `<div id="root">` 
 * from `index.html`, and injects the top-level `<App />` component into it.
 * This effectively hands over control of the webpage to React.
 */
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
    <App />
)
