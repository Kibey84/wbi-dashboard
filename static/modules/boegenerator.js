// modules/boeGenerator.js - BoE Generator management
export class BoeGeneratorManager {
    constructor({ state, notifications, utils }) {
        this.state = state;
        this.notifications = notifications;
        this.utils = utils;
        
        this.elements = {};
        this.debouncedCalculations = {};
        this.pollingInterval = null;
        
        // Bind methods
        this.initializeBoe = this.initializeBoe.bind(this);
        this.handleAIEstimate = this.handleAIEstimate.bind(this);
        this.handleGenerate = this.handleGenerate.bind(this);
        this.handleScopeFile = this.handleScopeFile.bind(this);
        this.addLaborRow = this.addLaborRow.bind(this);
        this.calculateTotalLaborHours = this.calculateTotalLaborHours.bind(this);
        this.calculateMaterials = this.calculateMaterials.bind(this);
        this.calculateTravel = this.calculateTravel.bind(this);
    }

    async init() {
        try {
            this.cacheElements();
            this.bindEvents();
            this.setupStateSubscriptions();
            this.setupDebouncedMethods();
            
            // Initialize BoE system
            await this.initializeBoe();
            
            console.log('[BoeGenerator] Manager initialized');
        } catch (error) {
            console.error('[BoeGenerator] Initialization failed:', error);
            throw error;
        }
    }

    cacheElements() {
        this.elements = {
            // File handling
            dropZone: document.getElementById('drop-zone'),
            dropZoneText: document.getElementById('drop-zone-text'),
            scope: document.getElementById('scope'),
            
            // Project info
            projectTitle: document.getElementById('projectTitle'),
            startDate: document.getElementById('startDate'),
            pop: document.getElementById('pop'),
            
            // Personnel
            personnelCheckboxes: document.getElementById('personnel-checkboxes'),
            
            // Tables
            laborTable: document.getElementById('labor-table'),
            materialsTable: document.getElementById('materials-table'),
            travelTable: document.getElementById('travel-table'),
            subcontractsTable: document.getElementById('subcontracts-table'),
            
            // Table controls
            addTaskBtn: document.getElementById('add-task-btn'),
            addMaterialBtn: document.getElementById('add-material-btn'),
            addTravelBtn: document.getElementById('add-travel-btn'),
            addSubcontractBtn: document.getElementById('add-subcontract-btn'),
            
            // AI controls
            aiEstimateBtn: document.getElementById('ai-estimate-btn'),
            aiSpinner: document.getElementById('ai-spinner'),
            aiBtnText: document.getElementById('ai-btn-text'),
            
            // Generation controls
            generateBtn: document.getElementById('generate-btn'),
            outputSection: document.getElementById('output-section'),
            completeState: document.getElementById('complete-state'),
            downloadExcel: document.getElementById('download-excel'),
            downloadPdf: document.getElementById('download-pdf'),
            
            // Summary displays
            totalLaborHours: document.getElementById('total-labor-hours')
        };
    }

    bindEvents() {
        // File drop zone
        if (this.elements.dropZone) {
            this.elements.dropZone.addEventListener('dragover', this.handleDragOver.bind(this));
            this.elements.dropZone.addEventListener('dragleave', this.handleDragLeave.bind(this));
            this.elements.dropZone.addEventListener('drop', this.handleDrop.bind(this));
        }

        // Tab switching for BoE sub-tabs
        document.querySelectorAll('.boe-tab-btn').forEach(button => {
            button.addEventListener('click', this.handleBoeTabSwitch.bind(this));
        });

        // Add buttons
        if (this.elements.addTaskBtn) {
            this.elements.addTaskBtn.addEventListener('click', () => {
                this.addLaborRow();
                this.debouncedCalculations.labor();
            });
        }

        // AI estimate button
        if (this.elements.aiEstimateBtn) {
            this.elements.aiEstimateBtn.addEventListener('click', this.handleAIEstimate);
        }

        // Generate button
        if (this.elements.generateBtn) {
            this.elements.generateBtn.addEventListener('click', this.handleGenerate);
        }

        // Table event delegation
        this.setupTableEvents();

        // Listen for initialization events
        document.addEventListener('initializeBoeGenerator', this.initializeBoe);
    }

    setupTableEvents() {
        // Labor table
        if (this.elements.laborTable) {
            this.elements.laborTable.addEventListener('input', this.debouncedCalculations.labor);
            this.elements.laborTable.addEventListener('click', this.handleTableClick.bind(this));
        }

        // Materials table
        if (this.elements.materialsTable) {
            this.elements.materialsTable.addEventListener('input', this.debouncedCalculations.materials);
            this.elements.materialsTable.addEventListener('click', this.handleTableClick.bind(this));
        }

        // Travel table
        if (this.elements.travelTable) {
            this.elements.travelTable.addEventListener('input', this.debouncedCalculations.travel);
            this.elements.travelTable.addEventListener('click', this.handleTableClick.bind(this));
        }

        // Subcontracts table
        if (this.elements.subcontractsTable) {
            this.elements.subcontractsTable.addEventListener('click', this.handleTableClick.bind(this));
        }

        // Add material button
        if (this.elements.addMaterialBtn) {
            this.elements.addMaterialBtn.addEventListener('click', () => {
                this.addTableRow('materials');
            });
        }

        // Add travel button
        if (this.elements.addTravelBtn) {
            this.elements.addTravelBtn.addEventListener('click', () => {
                this.addTableRow('travel');
            });
        }

        // Add subcontract button
        if (this.elements.addSubcontractBtn) {
            this.elements.addSubcontractBtn.addEventListener('click', () => {
                this.addTableRow('subcontracts');
            });
        }
    }

    setupStateSubscriptions() {
        // Subscribe to BoE state changes
        this.state.subscribe('boe', (boeState) => {
            this.updateUI(boeState);
        });

        this.state.subscribe('boe.aiJob', (aiJobState) => {
            this.updateAIButton(aiJobState);
        });
    }

    setupDebouncedMethods() {
        this.debouncedCalculations = {
            labor: this.utils.debounce(this.calculateTotalLaborHours, 300),
            materials: this.utils.debounce(this.calculateMaterials, 300),
            travel: this.utils.debounce(this.calculateTravel, 300)
        };
    }

    async initializeBoe() {
        try {
            console.log('[BoE] Initializing BoE Generator...');

            // Load logo
            await this.loadLogo();

            // Load labor rates
            await this.loadLaborRates();

            // Initialize tables
            this.populatePersonnelCheckboxes();
            this.populateLaborTable([]);

            // Enable AI estimate button
            if (this.elements.aiEstimateBtn) {
                this.elements.aiEstimateBtn.disabled = false;
            }

            console.log('[BoE] BoE Generator initialized successfully');

        } catch (error) {
            console.error('[BoE] Initialization failed:', error);
            this.notifications.error('Failed to initialize BoE Generator');
            throw error;
        }
    }

    async loadLogo() {
        try {
            const response = await fetch('/static/images/wbi-logo-horz.png');
            if (!response.ok) throw new Error('Logo not found');
            
            const blob = await response.blob();
            const logoBase64 = await this.utils.readFileAsDataURL(blob);
            
            this.state.set('boe.logoBase64', logoBase64);
            console.log('[BoE] Logo loaded successfully');
            
        } catch (error) {
            console.warn('[BoE] Could not load logo:', error);
            // Non-critical error, continue without logo
        }
    }

    async loadLaborRates() {
        try {
            const response = await this.utils.request('/api/rates');
            const laborRates = await response.json();
            
            this.state.set('boe.laborRates', laborRates);
            console.log('[BoE] Labor rates loaded:', Object.keys(laborRates).length, 'roles');
            
        } catch (error) {
            console.error('[BoE] Failed to load labor rates:', error);
            this.notifications.error('Failed to load labor rates');
            throw error;
        }
    }

    populatePersonnelCheckboxes() {
        if (!this.elements.personnelCheckboxes) return;

        const laborRates = this.state.get('boe.laborRates') || {};
        
        if (Object.keys(laborRates).length === 0) {
            this.elements.personnelCheckboxes.innerHTML = 
                '<p class="col-span-full text-red-500">Error: Could not load personnel roles.</p>';
            return;
        }

        const checkboxesHTML = Object.keys(laborRates).map(role => {
            const safeId = `personnel_${role.replace(/[^a-zA-Z0-9]/g, '')}`;
            return `
                <div>
                    <input type="checkbox" id="${safeId}" value="${role}" 
                           class="h-4 w-4 rounded border-gray-300 text-yellow-600 focus:ring-yellow-500" checked>
                    <label for="${safeId}" class="ml-2 text-sm">${this.utils.sanitizeHtml(role)}</label>
                </div>
            `;
        }).join('');

        this.elements.personnelCheckboxes.innerHTML = checkboxesHTML;
    }

    populateLaborTable(workPlan = []) {
        if (!this.elements.laborTable) return;

        const laborRates = this.state.get('boe.laborRates') || {};
        const availableRoles = Object.keys(laborRates);
        
        const thead = this.elements.laborTable.querySelector('thead');
        const tbody = this.elements.laborTable.querySelector('tbody');

        // Create header if not exists
        if (!thead.querySelector('tr')) {
            const headerHTML = `
                <tr>
                    <th class="p-2 text-left w-2/5">Work Breakdown Structure Element</th>
                    ${availableRoles.map(role => `<th class="p-2 text-sm">${this.utils.sanitizeHtml(role)}</th>`).join('')}
                    <th class="p-2 w-16">Actions</th>
                </tr>
            `;
            thead.innerHTML = headerHTML;
        }

        // Clear tbody
        tbody.innerHTML = '';

        if (!workPlan || workPlan.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="${availableRoles.length + 2}" class="p-4 text-center text-gray-500">
                        No labor tasks defined. Click "Add Task" to begin.
                    </td>
                </tr>
            `;
        } else {
            workPlan.forEach(item => {
                this.addLaborRow(item.task, item.hours);
            });
        }

        this.calculateTotalLaborHours();
    }

    addLaborRow(task = '', hours = {}) {
        if (!this.elements.laborTable) return;

        const laborRates = this.state.get('boe.laborRates') || {};
        const availableRoles = Object.keys(laborRates);
        const tbody = this.elements.laborTable.querySelector('tbody');

        // Remove placeholder row if it exists
        const placeholderRow = tbody.querySelector('td[colspan]');
        if (placeholderRow) {
            placeholderRow.closest('tr').remove();
        }

        const row = document.createElement('tr');
        row.className = 'border-b border-gray-200 hover:bg-gray-50';

        const hourInputs = availableRoles.map(role => `
            <td class="table-cell p-2">
                <input type="number" 
                       class="table-input w-full p-1 border border-gray-300 rounded text-center" 
                       value="${hours[role] || 0}" 
                       min="0" 
                       step="0.5"
                       aria-label="Hours for ${role}">
            </td>
        `).join('');

        row.innerHTML = `
            <td class="table-cell p-2">
                <input type="text" 
                       class="table-input w-full p-1 border border-gray-300 rounded" 
                       value="${this.utils.sanitizeHtml(task)}" 
                       placeholder="Enter task description"
                       aria-label="Task description">
            </td>
            ${hourInputs}
            <td class="table-cell text-center p-2">
                <button class="delete-btn bg-red-500 hover:bg-red-600 text-white px-2 py-1 rounded text-xs"
                        aria-label="Delete task">
                    ×
                </button>
            </td>
        `;

        tbody.appendChild(row);

        // Bind delete button
        const deleteBtn = row.querySelector('.delete-btn');
        deleteBtn.addEventListener('click', () => {
            row.remove();
            this.calculateTotalLaborHours();
            this.updateCurrentProjectData();
        });
    }

    addTableRow(tableType) {
        const tableMap = {
            materials: this.elements.materialsTable,
            travel: this.elements.travelTable,
            subcontracts: this.elements.subcontractsTable
        };

        const table = tableMap[tableType];
        if (!table) return;

        const tbody = table.querySelector('tbody');
        
        // Remove placeholder row if it exists
        const placeholderRow = tbody.querySelector('td[colspan]');
        if (placeholderRow) {
            placeholderRow.closest('tr').remove();
        }

        const newRow = document.createElement('tr');
        newRow.className = 'border-b border-gray-200 hover:bg-gray-50';
        
        switch (tableType) {
            case 'materials':
                newRow.innerHTML = this.getMaterialRowHTML();
                break;
            case 'travel':
                newRow.innerHTML = this.getTravelRowHTML();
                break;
            case 'subcontracts':
                newRow.innerHTML = this.getSubcontractRowHTML();
                break;
        }

        tbody.appendChild(newRow);

        // Bind delete button
        const deleteBtn = newRow.querySelector('.delete-btn');
        if (deleteBtn) {
            deleteBtn.addEventListener('click', () => {
                newRow.remove();
                if (tableType === 'materials') this.calculateMaterials();
                if (tableType === 'travel') this.calculateTravel();
                this.updateCurrentProjectData();
            });
        }
    }

    getMaterialRowHTML(partNum = '', desc = '', vendor = '', qty = 1, cost = 0) {
        return `
            <td class="table-cell p-2">
                <input type="text" class="table-input w-full p-1 border border-gray-300 rounded" 
                       value="${this.utils.sanitizeHtml(partNum)}" placeholder="Part number" aria-label="Part number">
            </td>
            <td class="table-cell p-2">
                <input type="text" class="table-input w-full p-1 border border-gray-300 rounded" 
                       value="${this.utils.sanitizeHtml(desc)}" placeholder="Description" aria-label="Description">
            </td>
            <td class="table-cell p-2">
                <input type="text" class="table-input w-full p-1 border border-gray-300 rounded" 
                       value="${this.utils.sanitizeHtml(vendor)}" placeholder="Vendor" aria-label="Vendor">
            </td>
            <td class="table-cell p-2">
                <input type="number" class="table-input w-full p-1 border border-gray-300 rounded text-center" 
                       value="${qty || 1}" min="1" step="1" aria-label="Quantity">
            </td>
            <td class="table-cell p-2">
                <input type="number" class="table-input w-full p-1 border border-gray-300 rounded text-center" 
                       value="${cost || 0}" min="0" step="0.01" aria-label="Unit cost">
            </td>
            <td class="p-2 text-right font-medium total-cost">${this.utils.formatCurrency(0)}</td>
            <td class="table-cell text-center p-2">
                <button class="delete-btn bg-red-500 hover:bg-red-600 text-white px-2 py-1 rounded text-xs" aria-label="Delete material">×</button>
            </td>
        `;
    }

    getTravelRowHTML(purpose = '', trips = 1, travelers = 1, days = 1, airfare = 0, lodging = 0, perDiem = 0) {
        return `
            <td class="table-cell p-2">
                <input type="text" class="table-input w-full p-1 border border-gray-300 rounded" 
                       value="${this.utils.sanitizeHtml(purpose)}" placeholder="Purpose" aria-label="Travel purpose">
            </td>
            <td class="table-cell p-2">
                <input type="number" class="table-input w-full p-1 border border-gray-300 rounded text-center" 
                       value="${trips || 1}" min="1" step="1" aria-label="Number of trips">
            </td>
            <td class="table-cell p-2">
                <input type="number" class="table-input w-full p-1 border border-gray-300 rounded text-center" 
                       value="${travelers || 1}" min="1" step="1" aria-label="Number of travelers">
            </td>
            <td class="table-cell p-2">
                <input type="number" class="table-input w-full p-1 border border-gray-300 rounded text-center" 
                       value="${days || 1}" min="1" step="1" aria-label="Number of days">
            </td>
            <td class="table-cell p-2">
                <input type="number" class="table-input w-full p-1 border border-gray-300 rounded text-center" 
                       value="${airfare || 0}" min="0" step="0.01" aria-label="Airfare cost">
            </td>
            <td class="table-cell p-2">
                <input type="number" class="table-input w-full p-1 border border-gray-300 rounded text-center" 
                       value="${lodging || 0}" min="0" step="0.01" aria-label="Lodging cost per night">
            </td>
            <td class="table-cell p-2">
                <input type="number" class="table-input w-full p-1 border border-gray-300 rounded text-center" 
                       value="${perDiem || 0}" min="0" step="0.01" aria-label="Per diem per day">
            </td>
            <td class="p-2 text-right font-medium total-cost">${this.utils.formatCurrency(0)}</td>
            <td class="table-cell text-center p-2">
                <button class="delete-btn bg-red-500 hover:bg-red-600 text-white px-2 py-1 rounded text-xs" aria-label="Delete travel">×</button>
            </td>
        `;
    }

    getSubcontractRowHTML(subcontractor = '', description = '', cost = 0) {
        return `
            <td class="table-cell p-2">
                <input type="text" class="table-input w-full p-1 border border-gray-300 rounded" 
                       value="${this.utils.sanitizeHtml(subcontractor)}" placeholder="Subcontractor" aria-label="Subcontractor name">
            </td>
            <td class="table-cell p-2">
                <input type="text" class="table-input w-full p-1 border border-gray-300 rounded" 
                       value="${this.utils.sanitizeHtml(description)}" placeholder="Description" aria-label="Work description">
            </td>
            <td class="table-cell p-2">
                <input type="number" class="table-input w-full p-1 border border-gray-300 rounded text-center" 
                       value="${cost || 0}" min="0" step="0.01" aria-label="Subcontract cost">
            </td>
            <td class="table-cell text-center p-2">
                <button class="delete-btn bg-red-500 hover:bg-red-600 text-white px-2 py-1 rounded text-xs" aria-label="Delete subcontract">×</button>
            </td>
        `;
    }

    calculateTotalLaborHours() {
        if (!this.elements.laborTable || !this.elements.totalLaborHours) return;

        let total = 0;
        const inputs = this.elements.laborTable.querySelectorAll('tbody input[type="number"]');
        
        inputs.forEach(input => {
            const value = parseFloat(input.value) || 0;
            total += value;
        });

        this.elements.totalLaborHours.textContent = this.utils.formatNumber(total);
        this.updateCurrentProjectData();
    }

    calculateMaterials() {
        if (!this.elements.materialsTable) return;

        const rows = this.elements.materialsTable.querySelectorAll('tbody tr');
        
        rows.forEach(row => {
            const inputs = row.querySelectorAll('input');
            const totalCell = row.querySelector('.total-cost');
            
            if (inputs.length >= 5 && totalCell) {
                const quantity = parseFloat(inputs[3].value) || 0;
                const unitCost = parseFloat(inputs[4].value) || 0;
                const total = quantity * unitCost;
                
                totalCell.textContent = this.utils.formatCurrency(total);
            }
        });

        this.updateCurrentProjectData();
    }

    calculateTravel() {
        if (!this.elements.travelTable) return;

        const rows = this.elements.travelTable.querySelectorAll('tbody tr');
        
        rows.forEach(row => {
            const inputs = row.querySelectorAll('input');
            const totalCell = row.querySelector('.total-cost');
            
            if (inputs.length >= 7 && totalCell) {
                const trips = parseFloat(inputs[1].value) || 0;
                const travelers = parseFloat(inputs[2].value) || 0;
                const days = parseFloat(inputs[3].value) || 0;
                const airfare = parseFloat(inputs[4].value) || 0;
                const lodging = parseFloat(inputs[5].value) || 0;
                const perDiem = parseFloat(inputs[6].value) || 0;
                
                const total = trips * travelers * (airfare + (lodging * days) + (perDiem * days));
                totalCell.textContent = this.utils.formatCurrency(total);
            }
        });

        this.updateCurrentProjectData();
    }

    updateCurrentProjectData() {
        const projectData = this.getCurrentProjectData();
        this.state.set('boe.currentProject', projectData);
        
        const totals = this.calculateAllTotals(projectData);
        this.state.set('boe.totals', totals);
    }

    getCurrentProjectData() {
        return {
            project_title: this.elements.projectTitle?.value || '',
            start_date: this.elements.startDate?.value || '',
            pop: this.elements.pop?.value || '',
            work_plan: this.extractWorkPlan(),
            materials_and_tools: this.extractMaterials(),
            travel: this.extractTravel(),
            subcontracts: this.extractSubcontracts()
        };
    }

    extractWorkPlan() {
        if (!this.elements.laborTable) return [];

        const rows = this.elements.laborTable.querySelectorAll('tbody tr');
        const laborRates = this.state.get('boe.laborRates') || {};
        const headers = Object.keys(laborRates);
        
        return Array.from(rows).map(row => {
            const placeholderCell = row.querySelector('td[colspan]');
            if (placeholderCell) return null;

            const inputs = row.querySelectorAll('input');
            if (inputs.length === 0) return null;

            const task = inputs[0].value || '';
            const hours = {};
            
            headers.forEach((header, index) => {
                const hourInput = inputs[index + 1];
                hours[header] = hourInput ? (parseFloat(hourInput.value) || 0) : 0;
            });

            return { task, hours };
        }).filter(Boolean);
    }

    extractMaterials() {
        if (!this.elements.materialsTable) return [];

        const rows = this.elements.materialsTable.querySelectorAll('tbody tr');
        
        return Array.from(rows).map(row => {
            const inputs = row.querySelectorAll('input');
            const placeholderCell = row.querySelector('td[colspan]');
            
            if (!inputs.length || placeholderCell) return null;

            return {
                part_number: inputs[0]?.value || '',
                description: inputs[1]?.value || '',
                vendor: inputs[2]?.value || '',
                quantity: parseFloat(inputs[3]?.value) || 0,
                unit_cost: parseFloat(inputs[4]?.value) || 0
            };
        }).filter(Boolean);
    }

    extractTravel() {
        if (!this.elements.travelTable) return [];

        const rows = this.elements.travelTable.querySelectorAll('tbody tr');
        
        return Array.from(rows).map(row => {
            const inputs = row.querySelectorAll('input');
            const placeholderCell = row.querySelector('td[colspan]');
            
            if (!inputs.length || placeholderCell) return null;

            return {
                purpose: inputs[0]?.value || '',
                trips: parseFloat(inputs[1]?.value) || 0,
                travelers: parseFloat(inputs[2]?.value) || 0,
                days: parseFloat(inputs[3]?.value) || 0,
                airfare: parseFloat(inputs[4]?.value) || 0,
                lodging: parseFloat(inputs[5]?.value) || 0,
                per_diem: parseFloat(inputs[6]?.value) || 0
            };
        }).filter(Boolean);
    }

    extractSubcontracts() {
        if (!this.elements.subcontractsTable) return [];

        const rows = this.elements.subcontractsTable.querySelectorAll('tbody tr');
        
        return Array.from(rows).map(row => {
            const inputs = row.querySelectorAll('input');
            const placeholderCell = row.querySelector('td[colspan]');
            
            if (!inputs.length || placeholderCell) return null;

            return {
                subcontractor: inputs[0]?.value || '',
                description: inputs[1]?.value || '',
                cost: parseFloat(inputs[2]?.value) || 0
            };
        }).filter(Boolean);
    }

    calculateAllTotals(projectData) {
        const laborRates = this.state.get('boe.laborRates') || {};
        const rates = this.state.get('boe.rates') || {};
        
        // Calculate labor cost
        let laborCost = 0;
        if (projectData.work_plan) {
            projectData.work_plan.forEach(task => {
                for (const role in task.hours) {
                    if (Object.prototype.hasOwnProperty.call(task.hours, role)) {
                        const hours = task.hours[role] || 0;
                        const rate = laborRates[role] || 0;
                        laborCost += hours * rate;
                    }
                }
            });
        }

        // Calculate materials cost
        const materialsCost = projectData.materials_and_tools 
            ? projectData.materials_and_tools.reduce((sum, item) => 
                sum + ((item.quantity || 0) * (item.unit_cost || 0)), 0)
            : 0;

        // Calculate travel cost
        const travelCost = projectData.travel 
            ? projectData.travel.reduce((sum, item) => {
                const trips = item.trips || 0;
                const travelers = item.travelers || 0;
                const days = item.days || 0;
                const airfare = item.airfare || 0;
                const lodging = item.lodging || 0;
                const perDiem = item.per_diem || 0;
                
                return sum + (trips * travelers * (airfare + (lodging * days) + (perDiem * days)));
            }, 0)
            : 0;

        // Calculate subcontract cost
        const subcontractCost = projectData.subcontracts 
            ? projectData.subcontracts.reduce((sum, item) => sum + (item.cost || 0), 0)
            : 0;

        // Calculate totals
        const totalDirectCosts = laborCost + materialsCost + travelCost + subcontractCost;
        const overheadAmount = laborCost * (rates.overhead || 0.17);
        const subtotal = totalDirectCosts + overheadAmount;
        const gnaAmount = subtotal * (rates.gna || 0.10);
        const totalCost = subtotal + gnaAmount;
        const feeAmount = totalCost * (rates.fee || 0.07);
        const totalPrice = totalCost + feeAmount;

        return {
            laborCost,
            materialsCost,
            travelCost,
            subcontractCost,
            totalDirectCosts,
            overheadAmount,
            subtotal,
            gnaAmount,
            totalCost,
            feeAmount,
            totalPrice
        };
    }

    async handleAIEstimate() {
        if (!this.elements.scope || !this.elements.pop || !this.elements.personnelCheckboxes) {
            this.notifications.error('Required form elements not found');
            return;
        }

        const scope = this.elements.scope.value || '';
        const pop = this.elements.pop.value || '';
        const selectedPersonnel = Array.from(
            this.elements.personnelCheckboxes.querySelectorAll('input:checked')
        ).map(cb => cb.value);

        if (selectedPersonnel.length === 0) {
            this.notifications.warning('Please select at least one personnel role');
            return;
        }

        if (!scope.trim()) {
            this.notifications.warning('Please enter a scope of work');
            return;
        }

        try {
            this.setAIButtonState(true);
            
            const newRequest = `**Scope of Work:** ${scope}
**Period of Performance:** ${pop} months
**Available Personnel:** ${selectedPersonnel.join(', ')}`;

            const caseHistory = '';

            console.log('[BoE] Starting AI estimation...');

            const response = await this.utils.request('/api/estimate', {
                method: 'POST',
                body: JSON.stringify({ new_request: newRequest, case_history: caseHistory }),
                timeout: 120000
            });

            const kickoffData = await response.json();

            if (!kickoffData.job_id) {
                throw new Error('No job ID returned from server');
            }

            console.log('[BoE] AI job started:', kickoffData.job_id);
            
            this.state.batch({
                'boe.aiJob.id': kickoffData.job_id,
                'boe.aiJob.status': 'running',
                'boe.aiJob.progress': 0
            });

            // Start polling
            const result = await this.pollAIJob(kickoffData.job_id);
            
            // Update UI with results
            this.updateUIFromData(result);
            this.notifications.success('AI estimation completed successfully');
            
            // Switch to labor tab
            const laborTabBtn = document.querySelector('button[data-boetab="labor"]');
            if (laborTabBtn) {
                laborTabBtn.click();
            }

        } catch (error) {
            console.error('[BoE] AI estimation failed:', error);
            this.notifications.error(`AI estimation failed: ${error.message}`);
        } finally {
            this.setAIButtonState(false);
            this.state.batch({
                'boe.aiJob.id': null,
                'boe.aiJob.status': 'idle',
                'boe.aiJob.progress': 0
            });
        }
    }

    async pollAIJob(jobId, maxWaitMs = 180000, intervalMs = 2000) {
        const start = Date.now();
        
        while (true) {
            try {
                const response = await this.utils.request(`/api/estimate/${jobId}`);
                const payload = await response.json();

                if (payload.log && Array.isArray(payload.log)) {
                    console.log('[BoE] Job progress:', payload.log.map(l => l.text).join(' | '));
                }

                if (payload.status === 'completed') {
                    return payload.result;
                }
                
                if (payload.status === 'failed') {
                    throw new Error(payload.error || 'AI estimation job failed');
                }

                if (Date.now() - start > maxWaitMs) {
                    throw new Error('AI estimation timed out');
                }

                await this.utils.sleep(intervalMs);

            } catch (error) {
                if (Date.now() - start > maxWaitMs) {
                    throw new Error('AI estimation timed out');
                }
                throw error;
            }
        }
    }

    updateUIFromData(projectData) {
        // Update form fields
        if (projectData.project_title && this.elements.projectTitle) {
            this.elements.projectTitle.value = projectData.project_title;
        }
        if (projectData.start_date && this.elements.startDate) {
            this.elements.startDate.value = projectData.start_date;
        }
        if (projectData.pop && this.elements.pop) {
            this.elements.pop.value = projectData.pop;
        }

        // Update tables
        if (projectData.work_plan) {
            this.populateLaborTable(projectData.work_plan);
        }
        if (projectData.materials_and_tools) {
            this.populateMaterialsTable(projectData.materials_and_tools);
        }
        if (projectData.travel) {
            this.populateTravelTable(projectData.travel);
        }
        if (projectData.subcontracts) {
            this.populateSubcontractsTable(projectData.subcontracts);
        }

        // Update state
        this.updateCurrentProjectData();
    }

    populateMaterialsTable(materials = []) {
        if (!this.elements.materialsTable) return;

        const tbody = this.elements.materialsTable.querySelector('tbody');
        tbody.innerHTML = '';

        if (!materials || materials.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="7" class="p-4 text-center text-gray-500">
                        No materials defined. Click "Add Material" to begin.
                    </td>
                </tr>
            `;
            return;
        }

        materials.forEach(item => {
            const newRow = document.createElement('tr');
            newRow.className = 'border-b border-gray-200 hover:bg-gray-50';
            newRow.innerHTML = this.getMaterialRowHTML(
                item.part_number, 
                item.description, 
                item.vendor, 
                item.quantity, 
                item.unit_cost
            );
            tbody.appendChild(newRow);

            // Bind delete button
            const deleteBtn = newRow.querySelector('.delete-btn');
            deleteBtn.addEventListener('click', () => {
                newRow.remove();
                this.calculateMaterials();
                this.updateCurrentProjectData();
            });
        });

        this.calculateMaterials();
    }

    populateTravelTable(travel = []) {
        if (!this.elements.travelTable) return;

        const tbody = this.elements.travelTable.querySelector('tbody');
        tbody.innerHTML = '';

        if (!travel || travel.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="9" class="p-4 text-center text-gray-500">
                        No travel defined. Click "Add Travel" to begin.
                    </td>
                </tr>
            `;
            return;
        }

        travel.forEach(item => {
            const newRow = document.createElement('tr');
            newRow.className = 'border-b border-gray-200 hover:bg-gray-50';
            newRow.innerHTML = this.getTravelRowHTML(
                item.purpose, 
                item.trips, 
                item.travelers, 
                item.days, 
                item.airfare, 
                item.lodging, 
                item.per_diem
            );
            tbody.appendChild(newRow);

            // Bind delete button
            const deleteBtn = newRow.querySelector('.delete-btn');
            deleteBtn.addEventListener('click', () => {
                newRow.remove();
                this.calculateTravel();
                this.updateCurrentProjectData();
            });
        });

        this.calculateTravel();
    }

    populateSubcontractsTable(subcontracts = []) {
        if (!this.elements.subcontractsTable) return;

        const tbody = this.elements.subcontractsTable.querySelector('tbody');
        tbody.innerHTML = '';

        if (!subcontracts || subcontracts.length === 0) {
            tbody.innerHTML = `
                <tr>
                    <td colspan="4" class="p-4 text-center text-gray-500">
                        No subcontracts defined. Click "Add Subcontract" to begin.
                    </td>
                </tr>
            `;
            return;
        }

        subcontracts.forEach(item => {
            const newRow = document.createElement('tr');
            newRow.className = 'border-b border-gray-200 hover:bg-gray-50';
            newRow.innerHTML = this.getSubcontractRowHTML(
                item.subcontractor, 
                item.description, 
                item.cost
            );
            tbody.appendChild(newRow);

            // Bind delete button
            const deleteBtn = newRow.querySelector('.delete-btn');
            deleteBtn.addEventListener('click', () => {
                newRow.remove();
                this.updateCurrentProjectData();
            });
        });
    }

    setAIButtonState(processing) {
        if (this.elements.aiEstimateBtn) {
            this.elements.aiEstimateBtn.disabled = processing;
        }
        
        if (this.elements.aiSpinner) {
            this.elements.aiSpinner.classList.toggle('hidden', !processing);
        }
        
        if (this.elements.aiBtnText) {
            this.elements.aiBtnText.textContent = processing ? 'Estimating...' : 'AI Estimate Full Project';
        }
    }

    updateAIButton(aiJobState) {
        this.setAIButtonState(aiJobState.status === 'running');
    }

    async handleGenerate() {
        if (!this.elements.generateBtn || !this.elements.outputSection || !this.elements.completeState) {
            this.notifications.error('Required elements not found');
            return;
        }

        try {
            this.elements.generateBtn.disabled = true;
            this.elements.outputSection.classList.remove('hidden');
            this.elements.completeState.classList.add('hidden');

            const projectData = this.getCurrentProjectData();
            const totals = this.calculateAllTotals(projectData);

            // Generate Excel file
            const excelResponse = await this.utils.request('/api/generate-boe-excel', {
                method: 'POST',
                body: JSON.stringify({ projectData, totals })
            });

            if (!excelResponse.ok) {
                throw new Error('Failed to generate Excel file');
            }

            const excelBlob = await excelResponse.blob();
            const excelUrl = URL.createObjectURL(excelBlob);
            
            if (this.elements.downloadExcel) {
                this.elements.downloadExcel.href = excelUrl;
                this.elements.downloadExcel.download = `BoE_${projectData.project_title.replace(/\s+/g, '_')}_Full.xlsx`;
            }

            // Generate PDF file
            const pdfResponse = await this.utils.request('/api/generate-boe-pdf', {
                method: 'POST',
                body: JSON.stringify({ projectData, totals })
            });

            if (!pdfResponse.ok) {
                throw new Error('Failed to generate PDF file');
            }

            const pdfBlob = await pdfResponse.blob();
            const pdfUrl = URL.createObjectURL(pdfBlob);
            
            if (this.elements.downloadPdf) {
                this.elements.downloadPdf.href = pdfUrl;
                this.elements.downloadPdf.download = `BoE_${projectData.project_title.replace(/\s+/g, '_')}_Customer.pdf`;
            }

            // Track URLs for cleanup
            this.utils.blobUrls.add(excelUrl);
            this.utils.blobUrls.add(pdfUrl);

            this.elements.completeState.classList.remove('hidden');
            this.notifications.success('BoE documents generated successfully');

        } catch (error) {
            console.error('[BoE] Generation failed:', error);
            this.notifications.error(`Failed to generate documents: ${error.message}`);
            this.elements.outputSection.classList.add('hidden');
        } finally {
            this.elements.generateBtn.disabled = false;
        }
    }

    handleDragOver(e) {
        e.preventDefault();
        if (this.elements.dropZone) {
            this.elements.dropZone.classList.add('dragover');
        }
    }

    handleDragLeave() {
        if (this.elements.dropZone) {
            this.elements.dropZone.classList.remove('dragover');
        }
    }

    handleDrop(e) {
        e.preventDefault();
        this.elements.dropZone?.classList.remove('dragover');
        
        const files = e.dataTransfer?.files;
        if (files && files.length > 0) {
            this.handleScopeFile(files[0]);
        }
    }

    async handleScopeFile(file) {
        if (!this.elements.dropZoneText || !this.elements.scope) return;

        this.elements.dropZoneText.textContent = `Processing: ${file.name}...`;

        try {
            let content = '';

            if (file.type === 'text/plain') {
                content = await this.utils.readFileAsText(file);
            } else if (file.name.endsWith('.docx')) {
                const buffer = await this.utils.readFileAsArrayBuffer(file);
                if (typeof mammoth !== 'undefined') {
                    const result = await mammoth.extractRawText({ arrayBuffer: buffer });
                    content = result.value;
                } else {
                    throw new Error('DOCX processing not available');
                }
            } else if (file.type === 'application/pdf') {
                if (typeof pdfjsLib !== 'undefined') {
                    const buffer = await this.utils.readFileAsArrayBuffer(file);
                    const pdf = await pdfjsLib.getDocument({ data: buffer }).promise;
                    
                    const pagePromises = [];
                    for (let i = 1; i <= pdf.numPages; i++) {
                        pagePromises.push(
                            pdf.getPage(i).then(page => 
                                page.getTextContent().then(textContent => 
                                    textContent.items.map(item => item.str).join(' ')
                                )
                            )
                        );
                    }
                    
                    const pageTexts = await Promise.all(pagePromises);
                    content = pageTexts.join('\n');
                } else {
                    throw new Error('PDF processing not available');
                }
            } else if (file.name.endsWith('.xlsx')) {
                if (typeof XLSX !== 'undefined') {
                    const buffer = await this.utils.readFileAsArrayBuffer(file);
                    const workbook = XLSX.read(buffer, { type: 'array' });
                    
                    const texts = [];
                    workbook.SheetNames.forEach(sheetName => {
                        const csv = XLSX.utils.sheet_to_csv(workbook.Sheets[sheetName]);
                        texts.push(csv);
                    });
                    content = texts.join('\n');
                } else {
                    throw new Error('Excel processing not available');
                }
            } else {
                throw new Error(`Unsupported file type: ${file.type || 'unknown'}`);
            }

            this.elements.scope.value = content;
            this.elements.dropZoneText.textContent = `Loaded: ${file.name}`;
            this.notifications.success(`File "${file.name}" processed successfully`);

        } catch (error) {
            console.error('[BoE] File processing failed:', error);
            this.notifications.error(`Failed to process file: ${error.message}`);
            this.elements.dropZoneText.textContent = 'Drag & drop or paste scope';
        }
    }

    handleTableClick(e) {
        if (e.target.classList.contains('delete-btn')) {
            const row = e.target.closest('tr');
            if (row) {
                row.remove();
                
                // Trigger appropriate calculations
                if (e.target.closest('#labor-table')) {
                    this.calculateTotalLaborHours();
                } else if (e.target.closest('#materials-table')) {
                    this.calculateMaterials();
                } else if (e.target.closest('#travel-table')) {
                    this.calculateTravel();
                }
                
                this.updateCurrentProjectData();
            }
        }
    }

    handleBoeTabSwitch(e) {
        const button = e.currentTarget;
        const tabId = button.dataset.boetab;
        
        if (!tabId) return;

        // Deactivate all BoE tabs
        document.querySelectorAll('.boe-tab-btn').forEach(btn => {
            btn.classList.remove('active');
        });
        document.querySelectorAll('.boe-tab-content').forEach(content => {
            content.classList.remove('active');
        });

        // Activate selected tab
        button.classList.add('active');
        const content = document.getElementById(tabId);
        if (content) {
            content.classList.add('active');
        }

        this.state.set('boe.activeSubTab', tabId);
    }

    updateUI(boeState) {
        // Update totals display if there's a totals section
        const totalsSection = document.getElementById('totals-section');
        if (totalsSection && boeState.totals) {
            this.updateTotalsDisplay(boeState.totals);
        }

        // Update labor rates display if needed
        if (boeState.laborRates && Object.keys(boeState.laborRates).length > 0) {
            this.populatePersonnelCheckboxes();
        }
    }

    updateTotalsDisplay(totals) {
        const totalsMap = {
            'labor-cost': totals.laborCost,
            'materials-cost': totals.materialsCost,
            'travel-cost': totals.travelCost,
            'subcontract-cost': totals.subcontractCost,
            'total-direct-costs': totals.totalDirectCosts,
            'overhead-amount': totals.overheadAmount,
            'subtotal': totals.subtotal,
            'gna-amount': totals.gnaAmount,
            'total-cost': totals.totalCost,
            'fee-amount': totals.feeAmount,
            'total-price': totals.totalPrice
        };

        Object.entries(totalsMap).forEach(([id, value]) => {
            const element = document.getElementById(id);
            if (element) {
                element.textContent = this.utils.formatCurrency(value);
            }
        });
    }

    // Public API methods
    getCurrentProject() {
        return this.state.get('boe.currentProject');
    }

    getTotals() {
        return this.state.get('boe.totals');
    }

    getLaborRates() {
        return this.state.get('boe.laborRates');
    }

    isAIJobRunning() {
        return this.state.get('boe.aiJob.status') === 'running';
    }

    getSelectedPersonnel() {
        if (!this.elements.personnelCheckboxes) return [];
        
        return Array.from(
            this.elements.personnelCheckboxes.querySelectorAll('input:checked')
        ).map(cb => cb.value);
    }

    // Cleanup
    destroy() {
        // Stop any polling
        if (this.pollingInterval) {
            clearInterval(this.pollingInterval);
            this.pollingInterval = null;
        }

        // Remove event listeners
        document.removeEventListener('initializeBoeGenerator', this.initializeBoe);

        // Clear blob URLs
        this.utils.cleanupBlobUrls();

        // Clear references
        this.elements = {};
        this.debouncedCalculations = {};
        this.state = null;
        this.notifications = null;
        this.utils = null;
    }
}