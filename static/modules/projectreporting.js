// modules/projectReporting.js - Project reporting management
export class ProjectReportingManager {
    constructor({ state, notifications, utils }) {
        this.state = state;
        this.notifications = notifications;
        this.utils = utils;
        
        this.elements = {};
        this.debouncedFetchUpdate = null;
        
        // Bind methods
        this.handlePmChange = this.handlePmChange.bind(this);
        this.handleStatusChange = this.handleStatusChange.bind(this);
        this.handleProjectSelect = this.handleProjectSelect.bind(this);
        this.handleMonthYearChange = this.handleMonthYearChange.bind(this);
        this.handleGenerateAndSave = this.handleGenerateAndSave.bind(this);
        this.populatePmFilter = this.populatePmFilter.bind(this);
    }

    async init() {
        try {
            this.cacheElements();
            this.bindEvents();
            this.setupStateSubscriptions();
            this.setupDebouncedMethods();
            
            // Initialize PM filter
            await this.populatePmFilter();
            
            console.log('[ProjectReporting] Manager initialized');
        } catch (error) {
            console.error('[ProjectReporting] Initialization failed:', error);
            throw error;
        }
    }

    cacheElements() {
        this.elements = {
            pmFilter: document.getElementById('pmFilter'),
            statusFilter: document.getElementById('statusFilter'),
            projectListMenu: document.getElementById('project-list-menu'),
            projectDetailView: document.getElementById('project-detail-view')
        };

        // Validate required elements
        const required = ['pmFilter', 'projectListMenu', 'projectDetailView'];
        const missing = required.filter(key => !this.elements[key]);
        
        if (missing.length > 0) {
            console.warn(`[ProjectReporting] Some elements missing: ${missing.join(', ')}`);
        }
    }

    bindEvents() {
        // PM filter change
        if (this.elements.pmFilter) {
            this.elements.pmFilter.addEventListener('change', this.handlePmChange);
        }

        // Status filter change
        if (this.elements.statusFilter) {
            this.elements.statusFilter.addEventListener('change', this.handleStatusChange);
        }

        // Project list clicks
        if (this.elements.projectListMenu) {
            this.elements.projectListMenu.addEventListener('click', this.handleProjectSelect);
        }

        // Month/Year changes in project detail view
        if (this.elements.projectDetailView) {
            this.elements.projectDetailView.addEventListener('change', this.handleMonthYearChange);
            this.elements.projectDetailView.addEventListener('click', this.handleGenerateAndSave);
        }

        // Listen for tab events to populate PM filter
        document.addEventListener('populatePmFilter', this.populatePmFilter);
    }

    setupStateSubscriptions() {
        // Subscribe to projects state changes
        this.state.subscribe('projects', (projectsState) => {
            this.updateUI(projectsState);
        });
    }

    setupDebouncedMethods() {
        // Debounce frequent operations
        this.debouncedFetchUpdate = this.utils.debounce(this.fetchAndPopulateUpdate.bind(this), 300);
    }

    async populatePmFilter() {
        if (!this.elements.pmFilter) return;

        try {
            this.state.set('projects.loading', true);
            
            const response = await this.utils.request('/api/pms');
            const pmNames = await response.json();
            
            // Clear existing options
            this.elements.pmFilter.innerHTML = '<option value="All">Select a Manager...</option>';
            
            // Add PM options
            pmNames.forEach(name => {
                if (name) {
                    const option = document.createElement('option');
                    option.value = name;
                    option.textContent = name;
                    this.elements.pmFilter.appendChild(option);
                }
            });

            console.log(`[ProjectReporting] Loaded ${pmNames.length} project managers`);
            
        } catch (error) {
            console.error('[ProjectReporting] Failed to load PMs:', error);
            this.notifications.error('Failed to load project managers');
        } finally {
            this.state.set('projects.loading', false);
        }
    }

    async loadProjectsForPm(pmName) {
        if (!pmName || pmName === 'All') {
            this.state.batch({
                'projects.data': [],
                'projects.selected': null,
                'projects.currentPM': null
            });
            return;
        }

        try {
            this.state.set('projects.loading', true);
            this.showProjectsLoading();

            const response = await this.utils.request(`/api/projects?pm=${encodeURIComponent(pmName)}`);
            const projects = await response.json();

            this.state.batch({
                'projects.data': projects,
                'projects.selected': null,
                'projects.currentPM': pmName
            });

            console.log(`[ProjectReporting] Loaded ${projects.length} projects for ${pmName}`);

        } catch (error) {
            console.error('[ProjectReporting] Failed to load projects:', error);
            this.notifications.error('Failed to load projects');
            this.showProjectsError(error.message);
        } finally {
            this.state.set('projects.loading', false);
        }
    }

    handlePmChange(e) {
        const pmName = e.target.value;
        this.state.set('projects.currentPM', pmName);
        this.loadProjectsForPm(pmName);
    }

    handleStatusChange(e) {
        const statusFilter = e.target.value;
        this.state.set('projects.statusFilter', statusFilter);
        this.processAndRenderProjects();
    }

    handleProjectSelect(e) {
        if (e.target.tagName === 'BUTTON' && e.target.dataset.projectName) {
            const projectName = e.target.dataset.projectName;
            this.selectProject(projectName);
        }
    }

    handleMonthYearChange(e) {
        if (e.target.classList.contains('month-select') || e.target.classList.contains('year-input')) {
            const selectedProject = this.state.get('projects.selected');
            if (selectedProject) {
                this.debouncedFetchUpdate(selectedProject);
            }
        }
    }

    async handleGenerateAndSave(e) {
        if (!e.target.classList.contains('generate-save-btn')) return;

        const button = e.target;
        const card = button.closest('.bg-gray-50');
        
        if (!card) {
            this.notifications.error('Could not find project form');
            return;
        }

        const projectName = button.dataset.projectName;
        const description = button.dataset.description;
        const month = card.querySelector('.month-select')?.value;
        const year = card.querySelector('.year-input')?.value;
        const managerUpdate = card.querySelector('.manager-update-textarea')?.value;
        const aiSummaryBox = card.querySelector('.ai-summary-box');

        if (!managerUpdate?.trim()) {
            this.notifications.warning('Please enter an update');
            return;
        }

        try {
            // Update UI
            button.disabled = true;
            button.textContent = 'Processing...';
            if (aiSummaryBox) {
                aiSummaryBox.textContent = 'Generating AI summary...';
            }

            const response = await this.utils.request('/api/update_project', {
                method: 'POST',
                body: JSON.stringify({
                    projectName,
                    description,
                    month,
                    year,
                    managerUpdate
                })
            });

            const result = await response.json();

            if (result.success) {
                if (aiSummaryBox) {
                    aiSummaryBox.textContent = result.aiSummary;
                }
                this.notifications.success('Project update saved successfully');
            } else {
                throw new Error(result.error || 'Unknown error');
            }

        } catch (error) {
            console.error('[ProjectReporting] Save failed:', error);
            this.notifications.error(`Failed to save update: ${error.message}`);
            if (aiSummaryBox) {
                aiSummaryBox.textContent = `Error: ${error.message}`;
            }
        } finally {
            button.disabled = false;
            button.textContent = 'Generate & Save';
        }
    }

    async selectProject(projectName) {
        if (projectName === this.state.get('projects.selected')) return;

        this.state.set('projects.selected', projectName);
        
        const projects = this.state.get('projects.data') || [];
        const project = projects.find(p => p.projectName === projectName);
        
        if (!project) {
            this.showProjectNotFound();
            return;
        }

        this.renderProjectDetail(project);
        await this.fetchAndPopulateUpdate(projectName);
        this.updateProjectListSelection(projectName);
    }

    async fetchAndPopulateUpdate(projectName) {
        if (!this.elements.projectDetailView || !projectName) return;

        const monthSelect = this.elements.projectDetailView.querySelector('.month-select');
        const yearInput = this.elements.projectDetailView.querySelector('.year-input');
        const managerUpdateBox = this.elements.projectDetailView.querySelector('.manager-update-textarea');
        const aiSummaryBox = this.elements.projectDetailView.querySelector('.ai-summary-box');

        if (!monthSelect || !yearInput || !managerUpdateBox || !aiSummaryBox) return;

        const month = monthSelect.value;
        const year = yearInput.value;

        // Clear current content
        managerUpdateBox.value = '';
        aiSummaryBox.textContent = '';

        try {
            const response = await this.utils.request(
                `/api/get_update?projectName=${encodeURIComponent(projectName)}&month=${month}&year=${year}`
            );
            const updateData = await response.json();

            managerUpdateBox.value = updateData.managerUpdate || '';
            aiSummaryBox.textContent = updateData.aiSummary || '';

        } catch (error) {
            console.error('[ProjectReporting] Failed to fetch update:', error);
            // Don't show notification for this - it's expected when no update exists
        }
    }

    processAndRenderProjects() {
        const currentPM = this.state.get('projects.currentPM');
        const statusFilter = this.state.get('projects.statusFilter') || 'All';
        const allProjects = this.state.get('projects.data') || [];

        if (!currentPM || currentPM === 'All') {
            this.showSelectPmMessage();
            return;
        }

        // Filter projects by status
        const filteredProjects = allProjects.filter(project => 
            statusFilter === 'All' || project.status === statusFilter
        );

        this.renderProjectList(filteredProjects);

        // Render detail for selected project or first project
        const selectedProject = this.state.get('projects.selected');
        let projectToDisplay = null;

        if (selectedProject) {
            projectToDisplay = filteredProjects.find(p => p.projectName === selectedProject);
        }
        
        if (!projectToDisplay && filteredProjects.length > 0) {
            projectToDisplay = filteredProjects[0];
        }

        if (projectToDisplay) {
            this.renderProjectDetail(projectToDisplay);
            this.fetchAndPopulateUpdate(projectToDisplay.projectName);
        } else {
            this.showNoProjectsMessage();
        }
    }

    renderProjectList(projects) {
        if (!this.elements.projectListMenu) return;

        this.elements.projectListMenu.innerHTML = '';

        if (projects.length === 0) {
            this.elements.projectListMenu.innerHTML = 
                '<p class="text-gray-500 text-sm">No projects match filters.</p>';
            return;
        }

        projects.forEach(project => {
            const menuItem = this.utils.createElement('button', {
                className: 'project-card-item block w-full text-left p-2 rounded-md text-gray-700 hover:bg-gray-200 focus:outline-none focus:ring-2 focus:ring-blue-500',
                dataset: { projectName: project.projectName },
                'aria-label': `Select project ${project.projectName}`
            }, [project.projectName]);

            // Highlight selected project
            const selectedProject = this.state.get('projects.selected');
            if (project.projectName === selectedProject) {
                menuItem.classList.add('bg-blue-100', 'font-semibold');
            }

            this.elements.projectListMenu.appendChild(menuItem);
        });
    }

    renderProjectDetail(project) {
        if (!this.elements.projectDetailView) return;

        this.elements.projectDetailView.innerHTML = this.createProjectCardHTML(project);
        this.state.set('projects.selected', project.projectName);
    }

    createProjectCardHTML(project) {
        const now = new Date();
        const currentYear = now.getFullYear();
        const currentMonth = now.toLocaleString('default', { month: 'long' });
        const months = [
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December"
        ];

        const optionsHtml = months
            .map(m => `<option ${m === currentMonth ? 'selected' : ''}>${m}</option>`)
            .join('');

        const statusBadge = project.status === 'Active'
            ? 'bg-green-100 text-green-800'
            : 'bg-gray-200 text-gray-800';

        const sanitizedDescription = this.utils.sanitizeHtml(project.description || 'No description available.');
        const sanitizedProjectName = this.utils.sanitizeHtml(project.projectName);
        const sanitizedPI = this.utils.sanitizeHtml(project.pi || 'N/A');
        const sanitizedPM = this.utils.sanitizeHtml(project.pm || 'N/A');
        const sanitizedEndDate = this.utils.sanitizeHtml(project.endDate || 'N/A');

        return `
            <div class="bg-white rounded-xl shadow-md p-6 border border-gray-200">
                <div class="flex justify-between items-start">
                    <h3 class="text-xl font-bold text-gray-800" data-project-name="${sanitizedProjectName}">${sanitizedProjectName}</h3>
                    <span class="px-3 py-1 text-sm font-medium rounded-full ${statusBadge}">${this.utils.sanitizeHtml(project.status)}</span>
                </div>
                <div class="text-sm text-gray-500 mt-2 border-b pb-4 mb-4">
                    <span><strong>PI:</strong> ${sanitizedPI}</span> |
                    <span><strong>PM:</strong> ${sanitizedPM}</span> |
                    <span><strong>End Date:</strong> ${sanitizedEndDate}</span>
                </div>
                <details class="mb-4" open>
                    <summary class="cursor-pointer font-medium text-blue-600 hover:underline focus:outline-none focus:ring-2 focus:ring-blue-500">Project Description</summary>
                    <p class="text-gray-600 mt-2 p-3 bg-gray-50 rounded-md">${sanitizedDescription}</p>
                </details>
                <div class="bg-gray-50 p-4 rounded-lg">
                    <h4 class="font-semibold text-gray-700 mb-3">Submit Monthly Update</h4>
                    <div class="grid grid-cols-1 md:grid-cols-3 gap-4 mb-3">
                        <div>
                            <label class="block text-sm font-medium text-gray-600">Month</label>
                            <select class="month-select mt-1 block w-full p-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-blue-500" aria-label="Select month">
                                ${optionsHtml}
                            </select>
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-600">Year</label>
                            <input type="number" class="year-input mt-1 block w-full p-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-blue-500" value="${currentYear}" min="2020" max="2030" aria-label="Select year">
                        </div>
                    </div>
                    <div>
                        <label class="block text-sm font-medium text-gray-600">Your Update</label>
                        <textarea class="manager-update-textarea mt-1 block w-full p-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500 focus:border-blue-500" rows="4" placeholder="Enter your monthly progress update here..." aria-label="Project update"></textarea>
                    </div>
                    <div class="mt-4">
                        <label class="block text-sm font-medium text-gray-600">AI-Generated Summary</label>
                        <div class="ai-summary-box mt-1 block w-full p-2 bg-white border border-gray-300 rounded-md min-h-[100px]" aria-label="AI generated summary"></div>
                    </div>
                    <button
                        class="generate-save-btn mt-4 w-full md:w-auto float-right bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded-lg transition duration-200 focus:outline-none focus:ring-2 focus:ring-blue-500"
                        data-project-name="${sanitizedProjectName}"
                        data-description="${this.utils.sanitizeHtml(project.description || '')}"
                        aria-label="Generate AI summary and save update">
                        Generate & Save
                    </button>
                </div>
            </div>
        `;
    }

    updateProjectListSelection(selectedProjectName) {
        if (!this.elements.projectListMenu) return;

        const buttons = this.elements.projectListMenu.querySelectorAll('button');
        buttons.forEach(button => {
            const isSelected = button.dataset.projectName === selectedProjectName;
            button.classList.toggle('bg-blue-100', isSelected);
            button.classList.toggle('font-semibold', isSelected);
            button.setAttribute('aria-pressed', isSelected);
        });
    }

    showProjectsLoading() {
        if (this.elements.projectListMenu) {
            this.elements.projectListMenu.innerHTML = '<p class="text-gray-500">Loading projects...</p>';
        }
    }

    showProjectsError(errorMessage) {
        if (this.elements.projectListMenu) {
            this.elements.projectListMenu.innerHTML = `<p class="text-red-500">Error: ${this.utils.sanitizeHtml(errorMessage)}</p>`;
        }
    }

    showSelectPmMessage() {
        if (this.elements.projectListMenu) {
            this.elements.projectListMenu.innerHTML = '<p class="text-gray-500 text-sm">Select a PM to see projects.</p>';
        }
        
        if (this.elements.projectDetailView) {
            this.elements.projectDetailView.innerHTML = `
                <div class="bg-white rounded-xl shadow-md p-6 border">
                    <p class="text-gray-500 text-center">Please select a Project Manager to begin.</p>
                </div>
            `;
        }
    }

    showNoProjectsMessage() {
        if (this.elements.projectDetailView) {
            this.elements.projectDetailView.innerHTML = `
                <div class="bg-white rounded-xl shadow-md p-6 border">
                    <p class="text-gray-500 text-center">No projects match the current filters.</p>
                </div>
            `;
        }
    }

    showProjectNotFound() {
        if (this.elements.projectDetailView) {
            this.elements.projectDetailView.innerHTML = `
                <div class="bg-white rounded-xl shadow-md p-6 border">
                    <p class="text-gray-500 text-center">Selected project not found.</p>
                </div>
            `;
        }
    }

    updateUI(projectsState) {
        if (projectsState.loading) {
            this.showProjectsLoading();
            return;
        }

        this.processAndRenderProjects();
    }

    // Public API methods
    getCurrentPM() {
        return this.state.get('projects.currentPM');
    }

    getSelectedProject() {
        return this.state.get('projects.selected');
    }

    getProjectData() {
        return this.state.get('projects.data') || [];
    }

    getFilteredProjects() {
        const allProjects = this.getProjectData();
        const statusFilter = this.state.get('projects.statusFilter') || 'All';
        
        return allProjects.filter(project => 
            statusFilter === 'All' || project.status === statusFilter
        );
    }

    refreshProjects() {
        const currentPM = this.getCurrentPM();
        if (currentPM && currentPM !== 'All') {
            this.loadProjectsForPm(currentPM);
        }
    }

    refreshCurrentProject() {
        const selectedProject = this.getSelectedProject();
        if (selectedProject) {
            this.fetchAndPopulateUpdate(selectedProject);
        }
    }

    // Statistics
    getStatistics() {
        const projects = this.getProjectData();
        const filteredProjects = this.getFilteredProjects();
        
        return {
            totalProjects: projects.length,
            filteredProjects: filteredProjects.length,
            currentPM: this.getCurrentPM(),
            selectedProject: this.getSelectedProject(),
            statusBreakdown: this.getStatusBreakdown(projects),
            isLoading: this.state.get('projects.loading')
        };
    }

    getStatusBreakdown(projects) {
        const breakdown = {};
        projects.forEach(project => {
            const status = project.status || 'Unknown';
            breakdown[status] = (breakdown[status] || 0) + 1;
        });
        return breakdown;
    }

    // Cleanup
    destroy() {
        // Remove event listeners
        if (this.elements.pmFilter) {
            this.elements.pmFilter.removeEventListener('change', this.handlePmChange);
        }

        if (this.elements.statusFilter) {
            this.elements.statusFilter.removeEventListener('change', this.handleStatusChange);
        }

        if (this.elements.projectListMenu) {
            this.elements.projectListMenu.removeEventListener('click', this.handleProjectSelect);
        }

        if (this.elements.projectDetailView) {
            this.elements.projectDetailView.removeEventListener('change', this.handleMonthYearChange);
            this.elements.projectDetailView.removeEventListener('click', this.handleGenerateAndSave);
        }

        document.removeEventListener('populatePmFilter', this.populatePmFilter);

        // Clear references
        this.elements = {};
        this.state = null;
        this.notifications = null;
        this.utils = null;
        this.debouncedFetchUpdate = null;
    }
}