(function() {
    const ID = 'viz-lifeline-badge';
    const CURSOR_ID = 'viz-lifeline-cursor';

    function ensureViz() {
        if (!document.body || document.getElementById(ID)) return;
        
        const style = document.createElement('style');
        style.textContent = `
            #${ID} {
                position: fixed !important; top: 10px !important; left: 10px !important; 
                width: 250px !important; height: 50px !important;
                background: rgba(255, 0, 0, 0.9) !important; 
                color: #ffffff !important; 
                font-family: 'Courier New', monospace !important;
                z-index: 2147483647 !important; pointer-events: none !important;
                display: flex !important; align-items: center !important;
                justify-content: center !important; padding: 5px !important; 
                font-size: 14px !important; font-weight: bold !important; 
                border: 2px solid yellow !important; border-radius: 4px !important;
                text-align: center !important;
            }
            #${CURSOR_ID} {
                position: fixed !important; width: 30px !important; height: 30px !important;
                border: 3px solid cyan !important; border-radius: 50% !important;
                background: rgba(0, 255, 255, 0.3) !important;
                z-index: 2147483646 !important; pointer-events: none !important;
                transform: translate(-50%, -50%) !important;
            }
        `;
        document.head.appendChild(style);

        const badge = document.createElement('div');
        badge.id = ID;
        const textSpan = document.createElement('span');
        textSpan.textContent = 'BRIDGE: STARTING...';
        badge.appendChild(textSpan);
        document.body.appendChild(badge);

        const cursor = document.createElement('div');
        cursor.id = CURSOR_ID;
        document.body.appendChild(cursor);
    }

    if (window.trustedTypes && window.trustedTypes.createPolicy && !window.trustedTypes.defaultPolicy) {
        try { window.trustedTypes.createPolicy('default', { createHTML: (s) => s, createScriptURL: (s) => s, createScript: (s) => s }); } catch (e) {}
    }

    setInterval(ensureViz, 500);
    ensureViz();

    window.__viz = {
        update: (s, isSuccess=false) => { 
            const el = document.querySelector(`#${ID} span`); 
            const badge = document.getElementById(ID);
            if (el) el.textContent = 'STATE: ' + s.toUpperCase(); 
            if (isSuccess && badge) {
                badge.style.background = 'rgba(0, 255, 0, 0.9)';
                badge.style.color = '#000';
            }
        },
        move: (x, y) => {
            const el = document.getElementById(CURSOR_ID);
            if (el) { el.style.left = x + 'px'; el.style.top = y + 'px'; }
        }
    };
})();
