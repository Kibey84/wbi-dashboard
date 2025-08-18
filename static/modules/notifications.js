// modules/notifications.js - User notification system
export class NotificationService {
    constructor() {
        this.container = null;
        this.notifications = new Map();
        this.queue = [];
        this.maxNotifications = 5;
        this.defaultDuration = 5000;
        this.initialized = false;
    }

    init() {
        if (this.initialized) return;
        
        this.createContainer();
        this.initialized = true;
        console.log('[Notifications] Service initialized');
    }

    createContainer() {
        // Remove existing container if it exists
        const existing = document.getElementById('notification-container');
        if (existing) {
            existing.remove();
        }

        this.container = document.createElement('div');
        this.container.id = 'notification-container';
        this.container.className = `
            fixed top-4 right-4 z-50 flex flex-col gap-2 max-w-sm w-full
            pointer-events-none
        `.trim().replace(/\s+/g, ' ');
        
        document.body.appendChild(this.container);
    }

    show(message, type = 'info', options = {}) {
        if (!this.initialized) {
            console.warn('[Notifications] Service not initialized, auto-initializing...');
            this.init();
        }

        const notification = {
            id: this.generateId(),
            message,
            type,
            timestamp: Date.now(),
            duration: options.duration || this.defaultDuration,
            persistent: options.persistent || false,
            actions: options.actions || []
        };

        // If we're at max capacity, remove oldest non-persistent notification
        if (this.notifications.size >= this.maxNotifications) {
            this.removeOldest();
        }

        this.notifications.set(notification.id, notification);
        this.render(notification);

        // Auto-remove if not persistent
        if (!notification.persistent && notification.duration > 0) {
            setTimeout(() => {
                this.remove(notification.id);
            }, notification.duration);
        }

        return notification.id;
    }

    success(message, options = {}) {
        return this.show(message, 'success', options);
    }

    error(message, options = {}) {
        return this.show(message, 'error', { 
            duration: 8000, // Longer duration for errors
            ...options 
        });
    }

    warning(message, options = {}) {
        return this.show(message, 'warning', options);
    }

    info(message, options = {}) {
        return this.show(message, 'info', options);
    }

    loading(message, options = {}) {
        return this.show(message, 'loading', { 
            persistent: true, 
            ...options 
        });
    }

    remove(id) {
        const notification = this.notifications.get(id);
        if (!notification) return;

        const element = document.getElementById(`notification-${id}`);
        if (element) {
            // Animate out
            element.style.transform = 'translateX(100%)';
            element.style.opacity = '0';
            
            setTimeout(() => {
                if (element.parentNode) {
                    element.parentNode.removeChild(element);
                }
                this.notifications.delete(id);
            }, 300);
        } else {
            this.notifications.delete(id);
        }
    }

    removeAll() {
        Array.from(this.notifications.keys()).forEach(id => {
            this.remove(id);
        });
    }

    removeOldest() {
        let oldest = null;
        let oldestTime = Date.now();

        this.notifications.forEach((notification, id) => {
            if (!notification.persistent && notification.timestamp < oldestTime) {
                oldest = id;
                oldestTime = notification.timestamp;
            }
        });

        if (oldest) {
            this.remove(oldest);
        }
    }

    update(id, updates) {
        const notification = this.notifications.get(id);
        if (!notification) return;

        // Update notification data
        Object.assign(notification, updates);
        this.notifications.set(id, notification);

        // Re-render the notification
        const element = document.getElementById(`notification-${id}`);
        if (element) {
            this.updateElement(element, notification);
        }
    }

    render(notification) {
        const element = this.createElement(notification);
        this.container.appendChild(element);

        // Animate in
        requestAnimationFrame(() => {
            element.style.transform = 'translateX(0)';
            element.style.opacity = '1';
        });
    }

    createElement(notification) {
        const element = document.createElement('div');
        element.id = `notification-${notification.id}`;
        element.className = `
            pointer-events-auto transform translate-x-full opacity-0 transition-all duration-300
            rounded-lg shadow-lg p-4 mb-2 max-w-sm w-full relative
            ${this.getTypeClasses(notification.type)}
        `.trim().replace(/\s+/g, ' ');

        element.innerHTML = this.getNotificationHTML(notification);
        this.bindEvents(element, notification);

        return element;
    }

    updateElement(element, notification) {
        const contentArea = element.querySelector('.notification-content');
        const actionsArea = element.querySelector('.notification-actions');
        
        if (contentArea) {
            contentArea.innerHTML = this.getContentHTML(notification);
        }
        
        if (actionsArea) {
            actionsArea.innerHTML = this.getActionsHTML(notification);
        }

        // Update classes
        element.className = `
            pointer-events-auto transform translate-x-0 opacity-100 transition-all duration-300
            rounded-lg shadow-lg p-4 mb-2 max-w-sm w-full relative
            ${this.getTypeClasses(notification.type)}
        `.trim().replace(/\s+/g, ' ');
    }

    getTypeClasses(type) {
        const classes = {
            success: 'bg-green-50 border-l-4 border-green-400 text-green-800',
            error: 'bg-red-50 border-l-4 border-red-400 text-red-800',
            warning: 'bg-yellow-50 border-l-4 border-yellow-400 text-yellow-800',
            info: 'bg-blue-50 border-l-4 border-blue-400 text-blue-800',
            loading: 'bg-gray-50 border-l-4 border-gray-400 text-gray-800'
        };
        return classes[type] || classes.info;
    }

    getNotificationHTML(notification) {
        return `
            <div class="flex items-start">
                <div class="flex-shrink-0">
                    ${this.getIcon(notification.type)}
                </div>
                <div class="ml-3 flex-1">
                    <div class="notification-content">
                        ${this.getContentHTML(notification)}
                    </div>
                    ${notification.actions.length > 0 ? `
                        <div class="notification-actions mt-2">
                            ${this.getActionsHTML(notification)}
                        </div>
                    ` : ''}
                </div>
                ${!notification.persistent ? `
                    <div class="ml-4 flex-shrink-0">
                        <button 
                            class="notification-close inline-flex text-gray-400 hover:text-gray-600 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500"
                            aria-label="Close notification"
                        >
                            <svg class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                                <path fill-rule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clip-rule="evenodd"></path>
                            </svg>
                        </button>
                    </div>
                ` : ''}
            </div>
        `;
    }

    getContentHTML(notification) {
        if (notification.type === 'loading') {
            return `
                <div class="flex items-center">
                    <div class="animate-spin rounded-full h-4 w-4 border-b-2 border-gray-600 mr-2"></div>
                    <p class="text-sm font-medium">${this.escapeHtml(notification.message)}</p>
                </div>
            `;
        }
        return `<p class="text-sm font-medium">${this.escapeHtml(notification.message)}</p>`;
    }

    getActionsHTML(notification) {
        return notification.actions.map(action => `
            <button 
                class="notification-action text-sm font-medium underline hover:no-underline focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 mr-2"
                data-action="${action.id}"
            >
                ${this.escapeHtml(action.label)}
            </button>
        `).join('');
    }

    getIcon(type) {
        const icons = {
            success: `
                <svg class="h-5 w-5 text-green-400" viewBox="0 0 20 20" fill="currentColor">
                    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"></path>
                </svg>
            `,
            error: `
                <svg class="h-5 w-5 text-red-400" viewBox="0 0 20 20" fill="currentColor">
                    <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd"></path>
                </svg>
            `,
            warning: `
                <svg class="h-5 w-5 text-yellow-400" viewBox="0 0 20 20" fill="currentColor">
                    <path fill-rule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clip-rule="evenodd"></path>
                </svg>
            `,
            info: `
                <svg class="h-5 w-5 text-blue-400" viewBox="0 0 20 20" fill="currentColor">
                    <path fill-rule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clip-rule="evenodd"></path>
                </svg>
            `,
            loading: `
                <div class="animate-spin rounded-full h-5 w-5 border-b-2 border-gray-400"></div>
            `
        };
        return icons[type] || icons.info;
    }

    bindEvents(element, notification) {
        // Close button
        const closeButton = element.querySelector('.notification-close');
        if (closeButton) {
            closeButton.addEventListener('click', () => {
                this.remove(notification.id);
            });
        }

        // Action buttons
        const actionButtons = element.querySelectorAll('.notification-action');
        actionButtons.forEach(button => {
            button.addEventListener('click', (e) => {
                const actionId = e.target.dataset.action;
                const action = notification.actions.find(a => a.id === actionId);
                if (action && typeof action.handler === 'function') {
                    try {
                        action.handler(notification.id);
                    } catch (error) {
                        console.error('[Notifications] Action handler error:', error);
                    }
                }
            });
        });
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    generateId() {
        return `notif_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    }

    // Progress notification utilities
    showProgress(message, initialProgress = 0) {
        const id = this.show(message, 'loading', { 
            persistent: true,
            progress: initialProgress 
        });
        
        return {
            id,
            update: (progress, newMessage) => {
                this.updateProgress(id, progress, newMessage);
            },
            complete: (message) => {
                this.completeProgress(id, message);
            },
            error: (message) => {
                this.errorProgress(id, message);
            }
        };
    }

    updateProgress(id, progress, message) {
        const notification = this.notifications.get(id);
        if (!notification) return;

        const updates = { progress: Math.max(0, Math.min(100, progress)) };
        if (message) {
            updates.message = message;
        }

        this.update(id, updates);
    }

    completeProgress(id, message = 'Completed') {
        this.update(id, {
            type: 'success',
            message,
            persistent: false,
            duration: 3000
        });
    }

    errorProgress(id, message = 'Failed') {
        this.update(id, {
            type: 'error',
            message,
            persistent: false,
            duration: 5000
        });
    }

    // Batch operations
    showBatch(notifications) {
        return notifications.map(notif => 
            this.show(notif.message, notif.type, notif.options)
        );
    }

    // Status checks
    hasNotification(id) {
        return this.notifications.has(id);
    }

    getNotification(id) {
        return this.notifications.get(id);
    }

    getNotificationCount() {
        return this.notifications.size;
    }

    getNotificationsByType(type) {
        return Array.from(this.notifications.values())
            .filter(notif => notif.type === type);
    }

    // Cleanup
    destroy() {
        this.removeAll();
        if (this.container && this.container.parentNode) {
            this.container.parentNode.removeChild(this.container);
        }
        this.container = null;
        this.notifications.clear();
        this.initialized = false;
    }
}