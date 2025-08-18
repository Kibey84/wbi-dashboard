// main.js - Entry point with module initialization
import { AppState } from './modules/state.js';
import { NotificationService } from './modules/notifications.js';
import { Utils } from './modules/utils.js';
import { TabManager } from './modules/tabManager.js';
import { PipelineManager } from './modules/pipeline.js';
import { OrgChartManager } from './modules/orgChart.js';
import { ProjectReportingManager } from './modules/projectReporting.js';
import { BoeGeneratorManager } from './modules/boeGenerator.js';

class Application {
    constructor() {
        this.state = new AppState();
        this.notifications = new NotificationService();
        this.utils = new Utils();
        this.managers = new Map();
        
        // Bind methods to preserve context
        this.init = this.init.bind(this);
        this.initializeManagers = this.initializeManagers.bind(this);
        this.handleError = this.handleError.bind(this);
    }

    async init() {
        try {
            // Wait for DOM to be ready
            if (document.readyState === 'loading') {
                await new Promise(resolve => {
                    document.addEventListener('DOMContentLoaded', resolve);
                });
            }

            console.log('[App] Initializing application...');
            
            // Initialize core services
            this.notifications.init();
            
            // Initialize tab management
            this.tabManager = new TabManager(this.state);
            
            // Initialize feature managers
            await this.initializeManagers();
            
            // Set up global error handling
            this.setupGlobalErrorHandling();
            
            console.log('[App] Application initialized successfully');
            
        } catch (error) {
            this.handleError('Application initialization failed', error);
        }
    }

    async initializeManagers() {
        const managerConfigs = [
            { name: 'pipeline', class: PipelineManager, dependencies: [] },
            { name: 'orgChart', class: OrgChartManager, dependencies: [] },
            { name: 'projectReporting', class: ProjectReportingManager, dependencies: [] },
            { name: 'boeGenerator', class: BoeGeneratorManager, dependencies: [] }
        ];

        // Initialize managers in dependency order
        for (const config of managerConfigs) {
            try {
                console.log(`[App] Initializing ${config.name} manager...`);
                
                const manager = new config.class({
                    state: this.state,
                    notifications: this.notifications,
                    utils: this.utils
                });
                
                await manager.init();
                this.managers.set(config.name, manager);
                
                console.log(`[App] ${config.name} manager initialized`);
                
            } catch (error) {
                console.error(`[App] Failed to initialize ${config.name} manager:`, error);
                this.notifications.error(`Failed to initialize ${config.name} feature: ${error.message}`);
            }
        }
    }

    setupGlobalErrorHandling() {
        // Handle unhandled promise rejections
        window.addEventListener('unhandledrejection', (event) => {
            console.error('[App] Unhandled promise rejection:', event.reason);
            this.notifications.error('An unexpected error occurred. Please refresh the page.');
            event.preventDefault();
        });

        // Handle general errors
        window.addEventListener('error', (event) => {
            console.error('[App] Global error:', event.error);
            this.notifications.error('An unexpected error occurred.');
        });
    }

    handleError(message, error) {
        console.error(`[App] ${message}:`, error);
        this.notifications.error(`${message}: ${error.message}`);
    }

    // Public API for accessing managers
    getManager(name) {
        return this.managers.get(name);
    }

    // Clean up resources
    destroy() {
        this.managers.forEach(manager => {
            if (typeof manager.destroy === 'function') {
                manager.destroy();
            }
        });
        this.managers.clear();
        this.notifications.destroy();
    }
}

// Initialize application
const app = new Application();
app.init();

// Export for global access if needed
window.WBIApp = app;

export default app;