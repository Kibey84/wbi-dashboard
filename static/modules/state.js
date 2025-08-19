// modules/state.js - Centralized state management
export class AppState {
    constructor() {
        this.subscribers = new Map();
        this.state = {
            // Application-wide state
            app: {
                initialized: false,
                loading: false
            },
            
            // Pipeline state
            pipeline: {
                currentJob: null,
                status: 'idle', // idle, running, completed, failed
                log: [],
                reports: {
                    opportunities: null,
                    matchmaking: null
                }
            },
            
            // Project reporting state
            projects: {
                data: [],
                selected: null,
                currentPM: null,
                statusFilter: 'All',
                loading: false
            },
            
            // BoE generator state
            boe: {
                laborRates: {},
                logoBase64: '',
                currentProject: {
                    project_title: '',
                    start_date: '',
                    pop: '',
                    work_plan: [],
                    materials_and_tools: [],
                    travel: [],
                    subcontracts: []
                },
                totals: {
                    laborCost: 0,
                    materialsCost: 0,
                    travelCost: 0,
                    subcontractCost: 0,
                    totalDirectCosts: 0,
                    overheadAmount: 0,
                    subtotal: 0,
                    gnaAmount: 0,
                    totalCost: 0,
                    feeAmount: 0,
                    totalPrice: 0
                },
                rates: {
                    overhead: 0.17,
                    gna: 0.10,
                    fee: 0.07
                },
                aiJob: {
                    id: null,
                    status: 'idle', // idle, running, completed, failed
                    progress: 0
                }
            },
            
            // Org chart state
            orgChart: {
                processing: false,
                lastProcessedFile: null,
                reportUrl: null
            }
        };
        
        // Performance optimization: debounced state updates
        this.debouncedNotify = this.debounce(this.notifySubscribers.bind(this), 50);
    }

    // Subscribe to state changes
    subscribe(path, callback) {
        if (!this.subscribers.has(path)) {
            this.subscribers.set(path, new Set());
        }
        this.subscribers.get(path).add(callback);
        
        // Return unsubscribe function
        return () => {
            const pathSubscribers = this.subscribers.get(path);
            if (pathSubscribers) {
                pathSubscribers.delete(callback);
                if (pathSubscribers.size === 0) {
                    this.subscribers.delete(path);
                }
            }
        };
    }

    // Get state value by path
    get(path) {
        return this.getNestedValue(this.state, path);
    }

    // Set state value by path
    set(path, value) {
        this.setNestedValue(this.state, path, value);
        this.debouncedNotify(path, value);
    }

    // Update state value by path (shallow merge for objects)
    update(path, updates) {
        const currentValue = this.get(path);
        let newValue;
        
        if (typeof currentValue === 'object' && currentValue !== null && !Array.isArray(currentValue)) {
            newValue = { ...currentValue, ...updates };
        } else {
            newValue = updates;
        }
        
        this.set(path, newValue);
    }

    // Reset state to initial values
    reset(path = null) {
        if (path) {
            this.setNestedValue(this.state, path, this.getInitialValue(path));
            this.debouncedNotify(path, this.get(path));
        } else {
            this.state = this.getInitialState();
            this.notifyAllSubscribers();
        }
    }

    // Batch multiple state updates
    batch(updates) {
        const notifications = [];
        
        Object.entries(updates).forEach(([path, value]) => {
            this.setNestedValue(this.state, path, value);
            notifications.push([path, value]);
        });
        
        // Notify all at once
        notifications.forEach(([path, value]) => {
            this.notifySubscribers(path, value);
        });
    }

    // Private helper methods
    getNestedValue(obj, path) {
        return path.split('.').reduce((current, key) => {
            return current && typeof current === 'object' ? current[key] : undefined;
        }, obj);
    }

    setNestedValue(obj, path, value) {
        const keys = path.split('.');
        const lastKey = keys.pop();
        const target = keys.reduce((current, key) => {
            if (!(key in current) || typeof current[key] !== 'object') {
                current[key] = {};
            }
            return current[key];
        }, obj);
        
        target[lastKey] = value;
    }

    notifySubscribers(path, value) {
        // Notify exact path subscribers
        const pathSubscribers = this.subscribers.get(path);
        if (pathSubscribers) {
            pathSubscribers.forEach(callback => {
                try {
                    callback(value, path);
                } catch (error) {
                    console.error(`[State] Subscriber error for path "${path}":`, error);
                }
            });
        }

        // Notify parent path subscribers
        const pathParts = path.split('.');
        for (let i = pathParts.length - 1; i > 0; i--) {
            const parentPath = pathParts.slice(0, i).join('.');
            const parentSubscribers = this.subscribers.get(parentPath);
            if (parentSubscribers) {
                const parentValue = this.get(parentPath);
                parentSubscribers.forEach(callback => {
                    try {
                        callback(parentValue, parentPath);
                    } catch (error) {
                        console.error(`[State] Parent subscriber error for path "${parentPath}":`, error);
                    }
                });
            }
        }
    }

    notifyAllSubscribers() {
        this.subscribers.forEach((subscribers, path) => {
            const value = this.get(path);
            subscribers.forEach(callback => {
                try {
                    callback(value, path);
                } catch (error) {
                    console.error(`[State] Subscriber error for path "${path}":`, error);
                }
            });
        });
    }

    getInitialState() {
        // Return a deep copy of the initial state
        return JSON.parse(JSON.stringify(this.state));
    }

    getInitialValue(path) {
        // This would need to be implemented based on your initial state structure
        const initialState = {
            'pipeline.status': 'idle',
            'pipeline.log': [],
            'projects.data': [],
            'projects.selected': null,
            'boe.currentProject': {
                project_title: '',
                start_date: '',
                pop: '',
                work_plan: [],
                materials_and_tools: [],
                travel: [],
                subcontracts: []
            }
        };
        
        return initialState[path] || null;
    }

    // Utility method for debouncing
    debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func.apply(this, args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    }

    // Debug methods
    getSubscriberCount(path = null) {
        if (path) {
            const pathSubscribers = this.subscribers.get(path);
            return pathSubscribers ? pathSubscribers.size : 0;
        }
        
        let total = 0;
        this.subscribers.forEach(subscribers => {
            total += subscribers.size;
        });
        return total;
    }

    dumpState() {
        return JSON.parse(JSON.stringify(this.state));
    }

    // State validation
    validate() {
        const errors = [];
        
        // Add validation rules as needed
        if (!this.state.boe || typeof this.state.boe !== 'object') {
            errors.push('BoE state is invalid');
        }
        
        if (!this.state.projects || typeof this.state.projects !== 'object') {
            errors.push('Projects state is invalid');
        }
        
        return {
            isValid: errors.length === 0,
            errors
        };
    }
}// Updated Tue Aug 19 09:34:46 EDT 2025
