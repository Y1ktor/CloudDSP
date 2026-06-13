// utils.js
// Reusable helper functions for UI formatting and dynamic CSS animation (marquee).
export const formatTime = (timeInSeconds) => {
    if (isNaN(timeInSeconds)) return "0:00";
    const minutes = Math.floor(timeInSeconds / 60);
    const seconds = Math.floor(timeInSeconds % 60);
    return `${minutes}:${seconds < 10 ? '0' : ''}${seconds}`;
};

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