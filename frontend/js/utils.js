// utils.js
// Reusable helper functions for UI formatting and dynamic CSS animation (marquee).

/**
 * Formats a raw time value in seconds into a standard M:SS string format.
 * Primarily used for updating the audio player's current time and duration display.
 * 
 * @param {number} timeInSeconds - The time value in seconds to format.
 * @returns {string} The formatted time string (e.g., "3:45"). Returns "0:00" if input is invalid.
 */
export const formatTime = (timeInSeconds) => {
    if (isNaN(timeInSeconds)) return "0:00";
    const minutes = Math.floor(timeInSeconds / 60);
    const seconds = Math.floor(timeInSeconds % 60);
    return `${minutes}:${seconds < 10 ? '0' : ''}${seconds}`;
};

/**
 * Dynamically applies a CSS marquee scrolling animation to a text element if its content overflows its container.
 * This ensures long file names (like imported automation files) are fully readable on small screens.
 * 
 * @param {HTMLElement} textElement - The DOM element containing the text.
 * @param {string} newText - The new text string to inject into the element.
 * @param {string} animPrefix - A unique prefix used to generate a distinct CSS @keyframes animation name.
 */
export const updateScrollingText = (textElement, newText, animPrefix) => {
    textElement.innerText = newText;
    textElement.style.animation = 'none';
    setTimeout(() => {
        const containerWidth = textElement.parentElement.clientWidth;
        const textWidth = textElement.scrollWidth;
        if (textWidth > containerWidth) {
            const animName = `${animPrefix}_${new Date().getTime()}`;
            const styleSheet = document.createElement('style');
            styleSheet.innerText = `
                @keyframes ${animName} {
                    0%, 15% { transform: translateX(0); }
                    85%, 100% { transform: translateX(-${textWidth - containerWidth + 10}px); }
                }
            `;
            document.head.appendChild(styleSheet);
            textElement.style.animation = `${animName} 4s linear infinite alternate`;
        }
    }, 50);
};