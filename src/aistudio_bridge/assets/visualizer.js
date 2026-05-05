(function() {
    const ID = 'viz-lifeline-badge';
    const CURSOR_ID = 'viz-lifeline-cursor';

    function ensureViz() {
        if (!document.body || document.getElementById(ID)) return;
        
        const style = document.createElement('style');
        style.textContent = `
            @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;700&display=swap');

            #${ID} {
                position: fixed !important; top: 15px !important; left: 15px !important; 
                min-width: 200px !important; padding: 12px 18px !important;
                background: rgba(10, 10, 20, 0.85) !important; 
                backdrop-filter: blur(8px) !important;
                color: #ffffff !important; 
                font-family: 'Outfit', -apple-system, sans-serif !important;
                z-index: 2147483647 !important; pointer-events: none !important;
                display: flex !important; flex-direction: column !important;
                gap: 4px !important;
                font-size: 13px !important; 
                border: 1.5px solid rgba(59, 130, 246, 0.5) !important;
                border-radius: 12px !important;
                box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4), 0 0 15px rgba(59, 130, 246, 0.3) !important;
                transition: all 0.3s ease !important;
            }
            #${ID}.state-success { border-color: rgba(34, 197, 94, 0.6) !important; box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4), 0 0 20px rgba(34, 197, 94, 0.4) !important; }
            #${ID}.state-success .value { color: #4ade80 !important; }
            
            #${ID}.state-warning { 
                border-color: rgba(234, 179, 8, 0.6) !important; 
                box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4), 0 0 20px rgba(234, 179, 8, 0.4) !important; 
                animation: WARNING-glow 2s infinite !important;
            }
            #${ID}.state-warning .value { color: #facc15 !important; }
            
            #${ID}.state-error { border-color: rgba(239, 68, 68, 0.7) !important; box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4), 0 0 25px rgba(239, 68, 68, 0.5) !important; animation: HUD-pulse 2s infinite !important; }
            #${ID}.state-error .value { color: #f87171 !important; }

            #${ID}.state-recovery { 
                border: 2px solid #ef4444 !important; 
                background: rgba(40, 5, 5, 0.95) !important;
                box-shadow: 0 0 40px rgba(239, 68, 68, 0.6) !important;
                animation: RECOVERY-glow 1.5s infinite !important;
            }
            #${ID}.state-recovery .value { color: #ff9999 !important; font-weight: 700 !important; }

            #${ID} .label { font-size: 10px !important; text-transform: uppercase !important; letter-spacing: 1px !important; opacity: 0.6 !important; font-weight: 700 !important; }
            #${ID} .value { font-weight: 400 !important; font-size: 14px !important; }
            
            #${ID} .req-indicator { 
                position: absolute !important; bottom: 15px !important; right: 15px !important;
                width: 8px !important; height: 8px !important; border-radius: 50% !important;
                background: #3b82f6 !important; box-shadow: 0 0 8px #3b82f6 !important;
                opacity: 0; transition: opacity 0.2s ease !important;
            }
            #${ID}.has-requests .req-indicator { opacity: 1 !important; animation: REQ-blink 1s infinite !important; }

            #${CURSOR_ID} {
                position: fixed !important; width: 34px !important; height: 34px !important;
                border: 2px solid rgba(59, 130, 246, 0.6) !important; border-radius: 50% !important;
                background: rgba(59, 130, 246, 0.1) !important;
                z-index: 2147483646 !important; pointer-events: none !important;
                transform: translate(-50%, -50%) !important;
                transition: transform 0.1s ease-out, opacity 0.3s !important;
                box-shadow: 0 0 10px rgba(59, 130, 246, 0.2) !important;
            }

            @keyframes HUD-pulse { 0% { opacity: 1; } 50% { opacity: 0.8; } 100% { opacity: 1; } }
            @keyframes WARNING-glow { 0% { box-shadow: 0 0 10px rgba(234, 179, 8, 0.2); } 50% { box-shadow: 0 0 25px rgba(234, 179, 8, 0.5); } 100% { box-shadow: 0 0 10px rgba(234, 179, 8, 0.2); } }
            @keyframes RECOVERY-glow { 0% { box-shadow: 0 0 20px rgba(239, 68, 68, 0.4); } 50% { box-shadow: 0 0 45px rgba(239, 68, 68, 0.8); } 100% { box-shadow: 0 0 20px rgba(239, 68, 68, 0.4); } }
            @keyframes REQ-blink { 0% { transform: scale(1); opacity: 1; } 50% { transform: scale(1.3); opacity: 0.7; } 100% { transform: scale(1); opacity: 1; } }
        `;
        document.head.appendChild(style);

        const badge = document.createElement('div');
        badge.id = ID;
        badge.innerHTML = `
            <div class="req-indicator"></div>
            <div class="label">AI Studio Bridge Status</div>
            <div class="value" id="${ID}-val">Initializing...</div>
        `;
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
        update: (text, type = 'neutral', hasRequests = false) => { 
            const val = document.getElementById(`${ID}-val`);
            const badge = document.getElementById(ID);
            if (!val || !badge) return;

            val.textContent = text;
            badge.className = ''; 
            if (type === 'success') badge.classList.add('state-success');
            if (type === 'warning') badge.classList.add('state-warning');
            if (type === 'error') badge.classList.add('state-error');
            if (type === 'recovery') badge.classList.add('state-recovery');
            if (hasRequests) badge.classList.add('has-requests');
        },
        move: (x, y) => {
            const el = document.getElementById(CURSOR_ID);
            if (el) { el.style.left = x + 'px'; el.style.top = y + 'px'; }
        }
    };
})();
