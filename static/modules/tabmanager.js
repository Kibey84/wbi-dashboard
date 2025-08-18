// modules/tabManager.js - Universal tab switching logic
export class TabManager {
    constructor(state) {
        this.state = state;
        this.activeTab = null;
        this.tabs = new Map();
        this.onTabChange = null;
        
        this.init();
    }

    init() {
        this.bindTabButtons();
        this.setInitialTab();
        console.log('[TabManager] Initialized');
    }

    bindTabButtons() {
        const tabButtons = document.querySelectorAll('.tab-button');
        
        tabButtons.forEach(button => {
            const tabId = button.getAttribute('data-tab');
            if (tabId) {
                this.tabs.set(tabId, {
                    button,
                    content: document.getElementById(tabId),
                    initialized: false
                });

                button.addEventListener('click', (e) => {
                    e.preventDefault();
                    this.switchToTab(tabId);
                });
            }
        });

        // Also handle BoE sub-tabs
        const boeTabButtons = document.querySelectorAll('.boe-tab-btn');
        boeTabButtons.forEach(button => {
            const tabId = button.getAttribute('data-boetab');
            if (tabId) {
                button.addEventListener('click', (e) => {
                    e.preventDefault();
                    this.switchToBoeTab(tabId);
                });
            }
        });
    }

    switchToTab(tabId) {
        const tab = this.tabs.get(tabId);
        if (!tab) {
            console.warn(`[TabManager] Tab "${tabId}" not found`);
            return;
        }

        try {
            // Deactivate all tabs
            this.tabs.forEach((tabData, id) => {
                tabData.button.classList.remove('active');
                if (tabData.content) {
                    tabData.content.classList.remove('active');
                }
            });

            // Activate selected tab
            tab.button.classList.add('active');
            if (tab.content) {
                tab.content.classList.add('active');
            }

            // Update state
            this.activeTab = tabId;
            this.state.set('app.activeTab', tabId);

            // Handle special tab initialization
            this.handleTabSpecialBehavior(tabId, tab);

            // Trigger callback if set
            if (typeof this.onTabChange === 'function') {
                this.onTabChange(tabId, tab);
            }

            console.log(`[TabManager] Switched to tab: ${tabId}`);

        } catch (error) {
            console.error(`[TabManager] Error switching to tab "${tabId}":`, error);
        }
    }

    switchToBoeTab(tabId) {
        try {
            // Deactivate all BoE tabs
            document.querySelectorAll('.boe-tab-btn').forEach(btn => {
                btn.classList.remove('active');
            });
            document.querySelectorAll('.boe-tab-content').forEach(content => {
                content.classList.remove('active');
            });

            // Activate selected BoE tab
            const button = document.querySelector(`[data-boetab="${tabId}"]`);
            const content = document.getElementById(tabId);

            if (button) button.classList.add('active');
            if (content) content.classList.add('active');

            this.state.set('boe.activeSubTab', tabId);

            console.log(`[TabManager] Switched to BoE tab: ${tabId}`);

        } catch (error) {
            console.error(`[TabManager] Error switching to BoE tab "${tabId}":`, error);
        }
    }

    handleTabSpecialBehavior(tabId, tab) {
        // Special handling for specific tabs
        switch (tabId) {
            case 'tab5': // Project reporting tab
                this.handleProjectTab(tab);
                break;
            case 'tab3': // BoE generator tab  
                this.handleBoeTab(tab);
                break;
            default:
                break;
        }
    }

    handleProjectTab(tab) {
        // Check if PM filter needs to be populated
        const pmFilter = document.getElementById('pmFilter');
        if (pmFilter && pmFilter.options.length <= 1) {
            // Trigger PM filter population
            const event = new CustomEvent('populatePmFilter');
            document.dispatchEvent(event);
        }
    }

    handleBoeTab(tab) {
        // Ensure BoE generator is initialized
        if (!tab.initialized) {
            const event = new CustomEvent('initializeBoeGenerator');
            document.dispatchEvent(event);
            tab.initialized = true;
        }
    }

    setInitialTab() {
        // Get initial tab from URL hash or default to first tab
        const hash = window.location.hash.slice(1);
        let initialTab = null;

        if (hash && this.tabs.has(hash)) {
            initialTab = hash;
        } else {
            // Find first available tab
            initialTab = Array.from(this.tabs.keys())[0];
        }

        if (initialTab) {
            this.switchToTab(initialTab);
        }
    }

    // Public API methods
    getActiveTab() {
        return this.activeTab;
    }

    getTab(tabId) {
        return this.tabs.get(tabId);
    }

    getAllTabs() {
        return Array.from(this.tabs.keys());
    }

    isTabActive(tabId) {
        return this.activeTab === tabId;
    }

    enableTab(tabId) {
        const tab = this.tabs.get(tabId);
        if (tab && tab.button) {
            tab.button.disabled = false;
            tab.button.classList.remove('disabled');
        }
    }

    disableTab(tabId) {
        const tab = this.tabs.get(tabId);
        if (tab && tab.button) {
            tab.button.disabled = true;
            tab.button.classList.add('disabled');
        }
    }

    showTabBadge(tabId, text, type = 'info') {
        const tab = this.tabs.get(tabId);
        if (tab && tab.button) {
            // Remove existing badges
            const existingBadge = tab.button.querySelector('.tab-badge');
            if (existingBadge) {
                existingBadge.remove();
            }

            // Add new badge
            const badge = document.createElement('span');
            badge.className = `tab-badge ml-2 px-2 py-1 text-xs rounded-full ${this.getBadgeClasses(type)}`;
            badge.textContent = text;
            tab.button.appendChild(badge);
        }
    }

    hideTabBadge(tabId) {
        const tab = this.tabs.get(tabId);
        if (tab && tab.button) {
            const badge = tab.button.querySelector('.tab-badge');
            if (badge) {
                badge.remove();
            }
        }
    }

    getBadgeClasses(type) {
        const classes = {
            info: 'bg-blue-100 text-blue-800',
            success: 'bg-green-100 text-green-800',
            warning: 'bg-yellow-100 text-yellow-800',
            error: 'bg-red-100 text-red-800'
        };
        return classes[type] || classes.info;
    }

    // URL hash management
    updateUrlHash(tabId) {
        if (history.replaceState) {
            history.replaceState(null, null, `#${tabId}`);
        } else {
            window.location.hash = tabId;
        }
    }

    // Event handling
    onTabChangeCallback(callback) {
        this.onTabChange = callback;
    }

    // Accessibility improvements
    setTabAccessibility() {
        this.tabs.forEach((tab, tabId) => {
            if (tab.button && tab.content) {
                // Set ARIA attributes
                tab.button.setAttribute('role', 'tab');
                tab.button.setAttribute('aria-controls', tabId);
                tab.button.setAttribute('aria-selected', this.activeTab === tabId);
                
                tab.content.setAttribute('role', 'tabpanel');
                tab.content.setAttribute('aria-labelledby', tab.button.id || `tab-${tabId}`);
                
                if (!tab.button.id) {
                    tab.button.id = `tab-${tabId}`;
                }
            }
        });
    }

    // Keyboard navigation
    handleKeyboardNavigation() {
        document.addEventListener('keydown', (e) => {
            if (e.target.classList.contains('tab-button')) {
                const tabs = Array.from(this.tabs.keys());
                const currentIndex = tabs.indexOf(this.activeTab);
                
                let newIndex = currentIndex;
                
                switch (e.key) {
                    case 'ArrowRight':
                    case 'ArrowDown':
                        newIndex = (currentIndex + 1) % tabs.length;
                        e.preventDefault();
                        break;
                    case 'ArrowLeft':
                    case 'ArrowUp':
                        newIndex = currentIndex > 0 ? currentIndex - 1 : tabs.length - 1;
                        e.preventDefault();
                        break;
                    case 'Home':
                        newIndex = 0;
                        e.preventDefault();
                        break;
                    case 'End':
                        newIndex = tabs.length - 1;
                        e.preventDefault();
                        break;
                    default:
                        return;
                }
                
                if (newIndex !== currentIndex) {
                    const newTabId = tabs[newIndex];
                    this.switchToTab(newTabId);
                    
                    const newTab = this.tabs.get(newTabId);
                    if (newTab && newTab.button) {
                        newTab.button.focus();
                    }
                }
            }
        });
    }

    // Animation support
    animateTabSwitch(outgoingTab, incomingTab) {
        return new Promise((resolve) => {
            if (!outgoingTab || !incomingTab) {
                resolve();
                return;
            }

            // Fade out current tab
            outgoingTab.style.opacity = '0';
            outgoingTab.style.transform = 'translateX(-20px)';
            
            setTimeout(() => {
                outgoingTab.classList.remove('active');
                incomingTab.classList.add('active');
                
                // Fade in new tab
                incomingTab.style.opacity = '0';
                incomingTab.style.transform = 'translateX(20px)';
                
                requestAnimationFrame(() => {
                    incomingTab.style.opacity = '1';
                    incomingTab.style.transform = 'translateX(0)';
                    
                    setTimeout(() => {
                        // Reset styles
                        outgoingTab.style.opacity = '';
                        outgoingTab.style.transform = '';
                        incomingTab.style.opacity = '';
                        incomingTab.style.transform = '';
                        resolve();
                    }, 300);
                });
            }, 150);
        });
    }

    // Cleanup
    destroy() {
        this.tabs.clear();
        this.activeTab = null;
        this.onTabChange = null;
    }
}