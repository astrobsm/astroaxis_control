import React, { useEffect, useMemo, useRef, useState, useCallback } from 'react';
import './styles.css';
import BulkUpload from './BulkUpload';
import ModuleInfo from './ModuleInfo';
import Geomap from './Geomap';
import RegulatoryCompliance from './RegulatoryCompliance';
import NetworkManagementDashboard from './NetworkManagementDashboard';
import { initOfflineEngine, subscribeOffline, pullFromCloud, processMutationQueue, clearOfflineCache } from './utils/offlineEngine';
import { requireLocation } from './utils/geo';
import { authedFetch, openAuthed } from './utils/api';

// Build stamp to verify fresh bundle after rebuilds
const BUILD_TAG = 'v2026.03.04-offline-first';

function AppMain({ currentUser = null, commUnread = { notices: 0, messages: {}, messages_total: 0 }, markNoticesSeen = () => {}, markMessagesSeen = () => {} }) {
 // Navigation and UI state
 const [activeModule, setActiveModule] = useState('dashboard');
 const [showForm, setShowForm] = useState(''); // which modal form is open
 const [editingItem, setEditingItem] = useState(null);
 const [loading, setLoading] = useState(false);
 const [notifications, setNotifications] = useState([]);
 const [sidebarVisible, setSidebarVisible] = useState(true); // sidebar toggle state

 // Offline-first sync status
 const [offlineStatus, setOfflineStatus] = useState({ online: navigator.onLine, pendingCount: 0, lastFullSync: null, syncing: false });
 const [pullProgress, setPullProgress] = useState(null); // { done, total, current }
 const [syncBarExpanded, setSyncBarExpanded] = useState(true); // auto-collapse sync bar
 const syncBarTimerRef = useRef(null);

 // Backend data cache
 const [data, setData] = useState({
 staff: [],
 products: [],
 rawMaterials: [],
 stock: [],
 warehouses: [],
 production: [],
 sales: [],
 attendance: [],
 customers: [],
 });

 const accessibleWarehouses = useMemo(() => {
 const allWarehouses = Array.isArray(data.warehouses) ? data.warehouses : [];
 if (!currentUser || currentUser.role === 'admin') {
 return allWarehouses;
 }
 return allWarehouses;
 }, [data.warehouses, currentUser]);

 const warehouseAlertIssuedRef = useRef(false);
 
 // Birthday notifications
 const [upcomingBirthdays, setUpcomingBirthdays] = useState([]);
 
 // Financial data (admin only)
 const [financialData, setFinancialData] = useState(null);
 
 // Salary & Payroll module state
 const [payrollDashboard, setPayrollDashboard] = useState(null);
 const [payrollEntries, setPayrollEntries] = useState([]);
 const [payrollTab, setPayrollTab] = useState('dashboard'); // dashboard | history | process
 const [payrollPeriod, setPayrollPeriod] = useState({
 start: new Date(new Date().getFullYear(), new Date().getMonth(), 1).toISOString().split('T')[0],
 end: new Date(new Date().getFullYear(), new Date().getMonth() + 1, 0).toISOString().split('T')[0],
 });
 const [payrollProcessing, setPayrollProcessing] = useState(false);

 // Stock Management state
 const [stockLevels, setStockLevels] = useState({ products: [], rawMaterials: [] });
 const [stockAnalysis, setStockAnalysis] = useState(null);
 const [stockDisplayArea, setStockDisplayArea] = useState('');
 
 // Customer unpaid orders state (for sales order form)
 const [customerUnpaidOrders, setCustomerUnpaidOrders] = useState(null);

 // Production & BOM state
 const [productionRequirements, setProductionRequirements] = useState(null);
 const [bomLines, setBomLines] = useState([]);
 const [selectedWarehouse, setSelectedWarehouse] = useState('');
 const [allBoms, setAllBoms] = useState([]);
 const [editingBomProductId, setEditingBomProductId] = useState(null);

 // Bulk Upload state
 const [showBulkUpload, setShowBulkUpload] = useState(null); // module name when modal open

 // Production Completions state
 const [prodCompletions, setProdCompletions] = useState([]);
 const [prodCompletionForm, setProdCompletionForm] = useState({
 product_id: '', production_date: new Date().toISOString().split('T')[0],
 qty_produced: '', qty_damaged: '0', damage_notes: '',
 staff_count: 0, total_hours_worked: 0, total_wages_paid: 0,
 energy_cost: '', lunch_cost: '', warehouse_id: '', notes: '',
 consumables: [], materials: []
 });
 const [prodStaffSummary, setProdStaffSummary] = useState(null);
 const [prodBomMaterials, setProdBomMaterials] = useState([]);

 // Production Consumables state
 const [prodConsumables, setProdConsumables] = useState([]);
 const [consumableForm, setConsumableForm] = useState({ name: '', unit: 'unit', unit_cost: '', category: '', description: '', current_stock: '', reorder_level: '' });
 const [editingConsumable, setEditingConsumable] = useState(null);
 const [lowStockConsumables, setLowStockConsumables] = useState([]);
 const [restockModal, setRestockModal] = useState(null);
 const [restockQty, setRestockQty] = useState('');

 // Machines & Equipment state
 const [machines, setMachines] = useState([]);
 const [machinesDashboard, setMachinesDashboard] = useState(null);
 const [machineForm, setMachineForm] = useState({
 name: '', equipment_type: '', serial_number: '', model: '', manufacturer: '',
 purchase_date: '', purchase_cost: '', current_value: '', depreciation_rate: '10',
 depreciation_method: 'straight_line', location: '', status: 'Operational', notes: ''
 });
 const [editingMachine, setEditingMachine] = useState(null);
 const [selectedMachine, setSelectedMachine] = useState(null);
 const [machineView, setMachineView] = useState('list'); // list, detail, maintenance, faults
 const [maintenanceForm, setMaintenanceForm] = useState({
 maintenance_type: 'routine', scheduled_date: '', description: '', cost: '', performed_by: '', notes: ''
 });
 const [faultForm, setFaultForm] = useState({
 fault_date: new Date().toISOString().split('T')[0], description: '', severity: 'Medium',
 status: 'Open', resolution: '', downtime_hours: '', repair_cost: '', reported_by: ''
 });
 const [machineMaintenanceRecords, setMachineMaintenanceRecords] = useState([]);
 const [machineFaults, setMachineFaults] = useState([]);
 const [machineDepreciation, setMachineDepreciation] = useState(null);

 // Marketing Module state
 const [mktDashboard, setMktDashboard] = useState(null);
 const [mktPlans, setMktPlans] = useState([]);
 const [mktLogs, setMktLogs] = useState([]);
 const [mktProposals, setMktProposals] = useState([]);
 const [mktProductsCatalog, setMktProductsCatalog] = useState([]);
 const [mktView, setMktView] = useState('dashboard');
 const [mktPlanForm, setMktPlanForm] = useState({ marketer_staff_id: '', week_start: '', week_end: '', title: '', objectives: '', target_areas: '', target_customers: '', planned_visits: '', planned_calls: '', budget_requested: '', status: 'submitted' });
 const [mktLogForm, setMktLogForm] = useState({ marketer_staff_id: '', log_date: new Date().toISOString().split('T')[0], start_time: '', end_time: '', location_visited: '', customer_contacted: '', contact_type: 'visit', objective: '', activities_performed: '', outcome: '', products_discussed: '', samples_distributed: '0', orders_generated: '0', order_value: '0', challenges: '', follow_up_required: false, follow_up_date: '', follow_up_notes: '', mood_rating: '3' });
 const [mktProposalForm, setMktProposalForm] = useState({ marketer_staff_id: '', title: '', proposal_type: 'campaign', target_audience: '', description: '', strategy: '', expected_outcome: '', budget_estimate: '', timeline_start: '', timeline_end: '', products_involved: '', channels: '', kpi_metrics: '', status: 'draft' });
 const [editingMktPlan, setEditingMktPlan] = useState(null);
 const [editingMktLog, setEditingMktLog] = useState(null);
 const [editingMktProposal, setEditingMktProposal] = useState(null);
 // Marketer facilities + performance
 const [mktFacilities, setMktFacilities] = useState([]);
 const [mktPerformance, setMktPerformance] = useState(null);
 const [mktPerfDays, setMktPerfDays] = useState(30);
 const [mktPerfMarketer, setMktPerfMarketer] = useState('');
 const [editingMktFacility, setEditingMktFacility] = useState(null);
 const [mktFacilityForm, setMktFacilityForm] = useState({ marketer_staff_id: '', facility_name: '', facility_type: 'hospital', address: '', city: '', state: '', contact_person: '', contact_title: '', contact_phone: '', contact_email: '', secondary_contact: '', secondary_phone: '', relationship_status: 'prospect', products_of_interest: '', last_visit_date: '', next_visit_date: '', visit_frequency: 'monthly', estimated_monthly_value: '0', notes: '' });

 // HR / Customer Care Module state
 const [hrDashboard, setHrDashboard] = useState(null);
 const [hrStaffList, setHrStaffList] = useState([]);
 const [hrPerformance, setHrPerformance] = useState([]);
 const [hrAttendanceLog, setHrAttendanceLog] = useState([]);
 const [hrProductsCatalog, setHrProductsCatalog] = useState([]);
 const [hrSalesOrders, setHrSalesOrders] = useState([]);
 const [hrCustomers, setHrCustomers] = useState([]);
 const [hrView, setHrView] = useState('dashboard');
 const [hrPerfDays, setHrPerfDays] = useState(30);

 // Customer (web) Orders state
 const [customerOrders, setCustomerOrders] = useState([]);
 const [customerOrdersLoading, setCustomerOrdersLoading] = useState(false);
 const [customerOrderLines, setCustomerOrderLines] = useState({}); // {orderId: [lines]}
 const [expandedCustomerOrder, setExpandedCustomerOrder] = useState(null);

 // Price List state
 const [showPriceList, setShowPriceList] = useState(false);

 // Payment Tracking & Debt Reconciliation state
 const [ptView, setPtView] = useState('dashboard');
 const [ptReconciliation, setPtReconciliation] = useState(null);
 const [ptInvoices, setPtInvoices] = useState([]);
 const [ptDebtors, setPtDebtors] = useState([]);
 const [ptReminders, setPtReminders] = useState([]);
 const [ptSelectedInvoice, setPtSelectedInvoice] = useState(null);
 const [ptSelectedDebtor, setPtSelectedDebtor] = useState(null);
 const [ptReminderMsg, setPtReminderMsg] = useState(null);
 const [ptPaymentForm, setPtPaymentForm] = useState({ amount: '', payment_method: 'bank_transfer', payment_date: new Date().toISOString().split('T')[0], reference: '', notes: '' });

 // Legacy / Previous Debts state
 const [legacyDebts, setLegacyDebts] = useState([]);
 const [legacyDebtsSummary, setLegacyDebtsSummary] = useState(null);
 const [legacyDebtDetail, setLegacyDebtDetail] = useState(null);
 const [legacyDebtForm, setLegacyDebtForm] = useState({ customer_id: '', description: '', original_amount: '', amount_already_paid: '0', debt_date: '', due_date: '', notes: '' });
 const [legacyPaymentForm, setLegacyPaymentForm] = useState({ amount: '', payment_method: 'bank_transfer', payment_date: new Date().toISOString().split('T')[0], reference: '', notes: '' });

 // Procurement Module state
 const [procView, setProcView] = useState('dashboard');
 const [procDashboard, setProcDashboard] = useState(null);
 const [procRequests, setProcRequests] = useState([]);
 const [procOrders, setProcOrders] = useState([]);
 const [procInvoices, setProcInvoices] = useState([]);
 const [procExpenses, setProcExpenses] = useState([]);
 const [procSelectedRequest, setProcSelectedRequest] = useState(null);
 const [procSelectedOrder, setProcSelectedOrder] = useState(null);
 const [procRequestForm, setProcRequestForm] = useState({
 requested_by: '', department: '', category: 'general', priority: 'normal',
 title: '', description: '', justification: '', vendor_name: '', vendor_contact: '',
 vendor_phone: '', vendor_email: '', expected_delivery_date: '', notes: '',
 items: [{ item_type: 'general', item_name: '', quantity: '1', unit: 'each', estimated_unit_cost: '', specification: '' }]
 });
 const [procOrderForm, setProcOrderForm] = useState({
 vendor_name: '', vendor_contact: '', vendor_phone: '', vendor_email: '', vendor_address: '',
 expected_delivery: '', tax_amount: '0', shipping_cost: '0', notes: '', created_by: '', request_id: '',
 items: [{ item_type: 'general', item_name: '', quantity: '1', unit: 'each', unit_cost: '', specification: '' }]
 });
 const [procExpenseForm, setProcExpenseForm] = useState({
 category: 'general', subcategory: '', description: '', amount: '',
 payment_method: '', payment_reference: '', payment_date: new Date().toISOString().split('T')[0],
 recipient: '', approved_by: '', notes: ''
 });
 const [procPoPayForm, setProcPoPayForm] = useState({ amount: '', payment_method: '', payment_reference: '' });

 // User Management state
 const [umFilter, setUmFilter] = useState('all');
 const [umSearch, setUmSearch] = useState('');
 const [umSelectedUser, setUmSelectedUser] = useState(null);
 const [umEditingRole, setUmEditingRole] = useState(null);

 // Module Access Control state
 const [moduleAccessData, setModuleAccessData] = useState([]); // all users' module access for admin grid
 const [myModuleAccess, setMyModuleAccess] = useState(null); // current user's module access
 const [maLoading, setMaLoading] = useState(false);
 const [maSaving, setMaSaving] = useState({});

 // Warehouse Access Control state
 const [warehouseAccessData, setWarehouseAccessData] = useState({ warehouses: [], users: [] });
 const [waLoading, setWaLoading] = useState(false);
 const [waSaving, setWaSaving] = useState({});

 // Transfer Module state
 const [transfersList, setTransfersList] = useState([]);
 const [transferSummary, setTransferSummary] = useState({ total_transfers: 0, total_quantity_moved: 0, today_transfers: 0 });
 const [transferForm, setTransferForm] = useState({ from_warehouse_id: '', to_warehouse_id: '', product_id: '', quantity: '', reason: '', notes: '' });
 const [showTransferForm, setShowTransferForm] = useState(false);
 const [transferLoading, setTransferLoading] = useState(false);

 // Returned Products Module state
 const [returnsList, setReturnsList] = useState([]);
 const [returnsSummary, setReturnsSummary] = useState({ total_returns: 0, total_quantity_returned: 0, pending_refunds: 0, total_refund_amount: 0, today_returns: 0, condition_breakdown: {} });
 const [returnForm, setReturnForm] = useState({ warehouse_id: '', product_id: '', sales_order_id: '', customer_id: '', quantity: '', return_reason: '', return_condition: 'good', refund_amount: '', notes: '', price_type: 'retail' });
 const [showReturnForm, setShowReturnForm] = useState(false);
 const [returnLoading, setReturnLoading] = useState(false);
 const [editingReturn, setEditingReturn] = useState(null);

 // Damaged Product Transfers Module state
 const [damagedTransfersList, setDamagedTransfersList] = useState([]);
 const [damagedTransfersSummary, setDamagedTransfersSummary] = useState({ total_transfers: 0, pending: 0, dispatched: 0, received: 0, total_quantity: 0 });
 const [damagedTransferForm, setDamagedTransferForm] = useState({ from_warehouse_id: '', to_warehouse_id: '', product_id: '', raw_material_id: '', quantity: '', damage_type: '', damage_reason: '', action_taken: '', notes: '' });
 const [showDamagedTransferForm, setShowDamagedTransferForm] = useState(false);
 const [damagedTransferLoading, setDamagedTransferLoading] = useState(false);

 // Receive Transfers Module state
 const [receiveTransfersList, setReceiveTransfersList] = useState([]);
 const [receiveTransfersSummary, setReceiveTransfersSummary] = useState({ pending_damaged_transfers: 0, received_damaged_transfers: 0, regular_completed_transfers: 0, pending_receipt_quantity: 0, total_all_transfers: 0 });
 const [receiveLoading, setReceiveLoading] = useState(false);
 const [showReceiveModal, setShowReceiveModal] = useState(null);
 const [receiveForm, setReceiveForm] = useState({ receipt_notes: '', receipt_condition: 'as_expected' });

 // Logistics Module state (Batch Manifest Workflow)
 const [logView, setLogView] = useState('dashboard');
 const [logDashboard, setLogDashboard] = useState(null);
 const [logManifests, setLogManifests] = useState([]);
 const [logAnalytics, setLogAnalytics] = useState(null);
 const [logSelectedManifest, setLogSelectedManifest] = useState(null);
 const [logConfirmForm, setLogConfirmForm] = useState({ receiver_name: '', receiver_phone: '', physical_invoice_number: '', delivery_notes: '', signature_collected: true });
 const [logConfirmingCustomerId, setLogConfirmingCustomerId] = useState(null);
 // Customer Management state
 const [custSearch, setCustSearch] = useState('');
 const [custShowForm, setCustShowForm] = useState(false);
 const [custEditing, setCustEditing] = useState(null);
 const [custForm, setCustForm] = useState({ name: '', email: '', phone: '', address: '', credit_limit: '0' });
 const [custViewOrders, setCustViewOrders] = useState(null);
 const [custOrders, setCustOrders] = useState([]);
 const [custLoadingOrders, setCustLoadingOrders] = useState(false);
 const [logManifestForm, setLogManifestForm] = useState({
 logistics_officer: '', delivery_date: new Date().toISOString().split('T')[0],
 vehicle_details: '', driver_name: '', driver_phone: '', transport_mode: 'vehicle',
 transport_cost: '', additional_charges: '0', notes: '',
 customers: [{ customer_id: '', customer_name: '', customer_phone: '', delivery_address: '', city: '', state: '',
 items: [{ product_id: '', product_name: '', sku: '', quantity: '1', unit: 'each' }] }]
 });

 // Communication Module state
 const [commView, setCommView] = useState('notices'); // notices | chat | videoConference
 const [commNotices, setCommNotices] = useState([]);
 const [commNoticeForm, setCommNoticeForm] = useState({ title: '', content: '', priority: 'normal', category: 'general' });
 const [commEditingNotice, setCommEditingNotice] = useState(null);
 const [commShowNoticeForm, setCommShowNoticeForm] = useState(false);
 const [commMessages, setCommMessages] = useState([]);
 const [commChatInput, setCommChatInput] = useState('');
 const [commChatChannel, setCommChatChannel] = useState('general');
 const [commMeetingRoom, setCommMeetingRoom] = useState('');
 const [commMeetingActive, setCommMeetingActive] = useState(false);
 const [commMeetingName, setCommMeetingName] = useState('');
 const commChatEndRef = useRef(null);
 const commJitsiRef = useRef(null);

 // Announcements Module state
 const [announcements, setAnnouncements] = useState([]);
 const [announcementPolicy, setAnnouncementPolicy] = useState(null);
 const [announcementForm, setAnnouncementForm] = useState({
 title:'', message:'', scheduled_date:'', scheduled_time:'09:00',
 repeat_type:'daily', repeat_days:'0,1,2,3,4', is_active:true,
 });
 const [editingAnnouncement, setEditingAnnouncement] = useState(null);
 const [announcementPreview, setAnnouncementPreview] = useState(null);
 const [announcementAudioFile, setAnnouncementAudioFile] = useState(null);
 const [announcementRecording, setAnnouncementRecording] = useState(false);
 const announcementAudioUrl = useMemo(() => (
 announcementAudioFile ? URL.createObjectURL(announcementAudioFile) : null
 ), [announcementAudioFile]);
 useEffect(() => () => { if (announcementAudioUrl) URL.revokeObjectURL(announcementAudioUrl); }, [announcementAudioUrl]);
 const announcementRecorderRef = useRef(null);
 const announcementChunksRef = useRef([]);
 const [clockInGreeting, setClockInGreeting] = useState(null);

 // ── Company Radio (persistent broadcast service) ───────────────────
 const [radioEnabled, setRadioEnabled] = useState(() => localStorage.getItem('radio_enabled') !== '0');
 const [radioMuted, setRadioMuted] = useState(() => localStorage.getItem('radio_muted') === '1');
 const [radioVolume, setRadioVolume] = useState(() => parseFloat(localStorage.getItem('radio_volume') || '0.85'));
 const [radioQueue, setRadioQueue] = useState([]);
 const [radioCurrent, setRadioCurrent] = useState(null);
 const [radioHistory, setRadioHistory] = useState([]);
 const [radioPanelOpen, setRadioPanelOpen] = useState(false);
 const [radioIsLeader, setRadioIsLeader] = useState(false);
 const radioAudioRef = useRef(null);
 const radioBcRef = useRef(null);
 const radioSinceRef = useRef(localStorage.getItem('radio_since') || new Date(Date.now() - 5 * 60 * 1000).toISOString());
 const radioSeenRef = useRef(new Set((() => { try { return JSON.parse(localStorage.getItem('radio_seen') || '[]'); } catch { return []; } })()));
 const radioUnlockedRef = useRef(false);

 // SOP Module state
 const [sopTemplates, setSopTemplates] = useState([]);
 const [sopSelectedCode, setSopSelectedCode] = useState('');
 const [sopRecords, setSopRecords] = useState([]);
 const [sopViewTab, setSopViewTab] = useState('document');
 const [sopSubmitting, setSopSubmitting] = useState(false);
 const [sopFormData, setSopFormData] = useState({
 operator_name: '',
 supervisor_name: '',
 executed_at: '',
 batch_number: '',
 material_equipment_used: '',
 checklist: {},
 numeric_inputs: {},
 operator_signature: '',
 supervisor_signature: '',
 comments: '',
 deviation: '',
 });

 // Form models
 const [forms, setForms] = useState({
 staff: {
 first_name: '', last_name: '', phone: '', date_of_birth: '',
 position: '', payment_mode: '', hourly_rate: '', monthly_salary: '', hire_date: '',
 bank_name: '', bank_account_number: '', bank_account_name: '', bank_currency: 'NGN'
 },
 product: { 
 sku: '', name: '', unit: 'each', description: '', manufacturer: '',
 reorder_level: '', selling_price: '', retail_price: '', wholesale_price: '',
 lead_time_days: '', minimum_order_quantity: '1',
 pricing: [{ unit: '', cost_price: '', retail_price: '', wholesale_price: '' }]
 },
 rawMaterial: { name: '', sku: '', manufacturer: '', unit: 'kg', reorder_level: '0', unit_cost: '', opening_stock: '0', date_added: new Date().toISOString().split('T')[0] },
 stockIntake: { product_id: '', warehouse_id: '', quantity: '', unit_cost: '', unit: '', supplier: '', batch_number: '', notes: '' },
 rawMaterialIntake: { raw_material_id: '', warehouse_id: '', quantity: '', unit_cost: '', supplier: '', batch_number: '', notes: '' },
 damagedProduct: { warehouse_id: '', product_id: '', quantity: '', damage_type: '', damage_reason: '', notes: '' },
 damagedRawMaterial: { warehouse_id: '', raw_material_id: '', quantity: '', damage_type: '', damage_reason: '', notes: '' },
 returnedProduct: { warehouse_id: '', product_id: '', quantity: '', return_reason: '', return_condition: 'good', customer_name: '', refund_amount: '0', notes: '' },
 warehouse: { code: '', name: '', location: '', manager_id: '' },
 customer: { customer_code: '', name: '', email: '', phone: '', address: '', credit_limit: '0' },
 payroll: { staff_id: '', pay_period_start: '', pay_period_end: '' },
 production: { product_id: '', quantity_to_produce: '', warehouse_id: '' }, // console quick action
 productionOrder: { product_id: '', quantity_planned: '', scheduled_start_date: '', scheduled_end_date: '', priority: 5, notes: '' },
 bom: { product_id: '', lines: [] },
 salesOrder: { customer_id: '', warehouse_id: '', required_date: '', notes: '', payment_status: 'unpaid', order_type: 'retail', lines: [] },
 });

 // Helpers
 const notify = (message, type = 'info') => {
 const id = Date.now();
 setNotifications((prev) => [...prev, { id, message, type }]);
 setTimeout(() => setNotifications((prev) => prev.filter((n) => n.id !== id)), 5000);
 };

 const formatCurrency = (n) => `₦${Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

 const extractItems = async (res) => {
 if (!res.ok) {
 const errorData = await res.json();
 const errorMsg = errorData.detail || 'Request failed';
 
 // If token expired, redirect to login
 if (res.status === 401 && errorMsg.toLowerCase().includes('expired')) {
 localStorage.removeItem('access_token');
 localStorage.removeItem('currentUser');
 window.location.href = '/';
 throw new Error('Session expired. Please login again.');
 }
 
 throw new Error(errorMsg);
 }
 const j = await res.json();
 if (j && typeof j === 'object' && Array.isArray(j.items)) return j.items;
 if (Array.isArray(j)) return j;
 return j;
 };

 // Initialize offline-first engine on mount
 useEffect(() => {
 initOfflineEngine().then(() => {
 console.log('[App] Offline engine ready');
 }).catch(err => console.warn('[App] Offline engine init:', err));
 const unsub = subscribeOffline(setOfflineStatus);
 return unsub;
 }, []);

 // Auto-collapse sync bar after 3s when online with no pending changes
 useEffect(() => {
 if (syncBarTimerRef.current) clearTimeout(syncBarTimerRef.current);
 if (offlineStatus.online && offlineStatus.pendingCount === 0 && !offlineStatus.syncing && !pullProgress) {
 syncBarTimerRef.current = setTimeout(() => setSyncBarExpanded(false), 3000);
 } else {
 // Keep expanded when offline, syncing, or has pending changes
 setSyncBarExpanded(true);
 }
 return () => { if (syncBarTimerRef.current) clearTimeout(syncBarTimerRef.current); };
 }, [offlineStatus.online, offlineStatus.pendingCount, offlineStatus.syncing, pullProgress]);

 // Pull from Cloud handler
 const handlePullFromCloud = useCallback(async () => {
 if (!offlineStatus.online) { notify('Cannot pull while offline', 'error'); return; }
 try {
 setPullProgress({ done: 0, total: 0, current: '' });
 const result = await pullFromCloud((progress) => setPullProgress(progress));
 notify(`Synced ${result.cached} of ${result.total} data sources for offline use` + (result.errors ? ` (${result.errors} failed)` : ''), result.errors ? 'warning' : 'success');
 // Refresh the in-memory data
 fetchAllData();
 } catch (e) {
 notify(`Pull failed: ${e.message}`, 'error');
 } finally {
 setPullProgress(null);
 }
 }, [offlineStatus.online]);

 // Sync queued mutations handler
 const handleSyncNow = useCallback(async () => {
 if (!offlineStatus.online) { notify('Cannot sync while offline', 'error'); return; }
 const result = await processMutationQueue();
 if (result.success > 0) {
 notify(`Synced ${result.success} queued changes to cloud`, 'success');
 fetchAllData();
 } else if (result.failed > 0) {
 notify(`${result.failed} changes failed to sync`, 'error');
 } else {
 notify('No pending changes to sync', 'info');
 }
 }, [offlineStatus.online]);

 // Listen for service worker sync-complete messages
 useEffect(() => {
 const handler = (event) => {
 if (event.data?.type === 'SYNC_COMPLETE') {
 fetchAllData();
 notify('Background sync completed', 'success');
 }
 };
 navigator.serviceWorker?.addEventListener('message', handler);
 return () => navigator.serviceWorker?.removeEventListener('message', handler);
 }, []);

 // Fetch all data on load
 useEffect(() => {
 fetchAllData();
 fetchUpcomingBirthdays();
 }, []);

 // Fetch data when switching to new modules
 useEffect(() => {
 if (activeModule === 'consumables') { fetchConsumables(); fetchLowStockConsumables(); }
 if (activeModule === 'production') { fetchAllBoms(); }
 if (activeModule === 'productionCompletions') { fetchProdCompletions(); fetchConsumables(); }
 if (activeModule === 'machinesEquipment') { fetchMachines(); fetchMachinesDashboard(); }
 if (activeModule === 'marketing') { fetchMktDashboard(); fetchMktPlans(); fetchMktLogs(); fetchMktProposals(); fetchMktProducts(); fetchMktFacilities(); fetchMktPerformance(); }
 if (activeModule === 'hrCustomerCare') { fetchHrDashboard(); fetchHrStaff(); fetchHrPerformance(); fetchHrProducts(); fetchHrSalesOrders(); fetchHrCustomers(); fetchHrAttendance(); }
 if (activeModule === 'paymentTracking') { fetchPtReconciliation(); fetchPtInvoices(); fetchPtDebtors(); fetchPtReminders(); fetchLegacyDebts(); }
 if (activeModule === 'procurement') { fetchProcDashboard(); fetchProcRequests(); fetchProcOrders(); fetchProcInvoices(); fetchProcExpenses(); }
 if (activeModule === 'logistics') { fetchLogDashboard(); fetchLogManifests(); fetchLogAnalytics(); }
 if (activeModule === 'transfers') { fetchTransfers(); fetchTransferSummary(); }
 if (activeModule === 'returns') { fetchReturns(); fetchReturnsSummary(); }
 if (activeModule === 'damagedTransfers') { fetchDamagedTransfers(); fetchDamagedTransfersSummary(); }
 if (activeModule === 'receiveTransfers') { fetchReceiveTransfers(); fetchReceiveTransfersSummary(); }
 if (activeModule === 'salaryPayroll') { fetchPayrollDashboard(); fetchPayrollEntries(); }
 if (activeModule === 'customers') { fetchData('customers'); }
 if (activeModule === 'customerOrders') { fetchCustomerOrders(); }
 if (activeModule === 'announcements') {
 authedFetch('/api/announcements/').then(r=>r.ok?r.json():[]).then(d=>setAnnouncements(Array.isArray(d)?d:[])).catch(()=>{});
 authedFetch('/api/announcements/policy').then(r=>r.ok?r.json():null).then(setAnnouncementPolicy).catch(()=>{});
 }
 }, [activeModule]);

 // NOTE: Scheduled announcements are NOT played by a separate poller anymore.
 // The Company Radio service (below) is the single source of truth and the single
 // audio channel for ALL spoken output — including due announcements, which the
 // /api/radio/feed endpoint already emits as `type: 'announcement'` events. This
 // removes the previous conflict where the same announcement played twice (once by
 // the standalone /api/announcements/due poller and once by the radio queue).

 // ── Company Radio: leader election + polling ───────────────────────
 useEffect(() => {
 if (!radioEnabled) return;
 let stopped = false;
 let pollTimer = null;
 const bc = (typeof BroadcastChannel !== 'undefined') ? new BroadcastChannel('astrobsm-radio') : null;
 radioBcRef.current = bc;
 if (bc) {
 bc.onmessage = (ev) => {
 const m = ev.data;
 if (m?.type === 'event') {
 // Mirror history on follower tabs (no playback there)
 setRadioHistory(h => [m.event, ...h.filter(x => x.id !== m.event.id)].slice(0, 50));
 }
 };
 }
 async function pollOnce() {
 try {
 const since = radioSinceRef.current;
 const r = await authedFetch(`/api/radio/feed?since=${encodeURIComponent(since)}&limit=50`);
 if (!r.ok) return;
 const data = await r.json();
 radioSinceRef.current = data.now;
 localStorage.setItem('radio_since', data.now);
 const fresh = (data.events || []).filter(e => !radioSeenRef.current.has(e.id));
 if (!fresh.length) return;
 // play oldest first
 fresh.reverse();
 for (const ev of fresh) {
 radioSeenRef.current.add(ev.id);
 if (bc) { try { bc.postMessage({ type: 'event', event: ev }); } catch {} }
 }
 setRadioQueue(q => [...q, ...fresh]);
 setRadioHistory(h => {
 const merged = [...fresh.slice().reverse(), ...h];
 const dedup = []; const seen = new Set();
 for (const e of merged) { if (!seen.has(e.id)) { seen.add(e.id); dedup.push(e); } }
 return dedup.slice(0, 50);
 });
 // trim seen set
 if (radioSeenRef.current.size > 600) {
 const arr = Array.from(radioSeenRef.current).slice(-400);
 radioSeenRef.current = new Set(arr);
 }
 localStorage.setItem('radio_seen', JSON.stringify(Array.from(radioSeenRef.current).slice(-400)));
 } catch (e) { /* network hiccup, retry next tick */ }
 }
 function startPolling() {
 setRadioIsLeader(true);
 pollOnce();
 pollTimer = setInterval(() => { if (!stopped) pollOnce(); }, 20000);
 }
 // Use Web Locks to ensure only one tab polls; fallback otherwise
 if (navigator.locks?.request) {
 navigator.locks.request('astrobsm-radio-leader', { mode: 'exclusive' }, () => {
 if (stopped) return;
 startPolling();
 // Hold the lock until tab closes / effect tears down
 return new Promise(resolve => { stopped && resolve(); const iv = setInterval(() => { if (stopped) { clearInterval(iv); resolve(); } }, 1000); });
 }).catch(() => { if (!stopped) startPolling(); });
 } else {
 startPolling();
 }
 return () => {
 stopped = true;
 if (pollTimer) clearInterval(pollTimer);
 if (bc) { try { bc.close(); } catch {} }
 setRadioIsLeader(false);
 };
 }, [radioEnabled]);

 // ── Company Radio: queue playback (single audio channel) ───────────
 // Plays exactly one item at a time. Because every spoken event — including
 // scheduled announcements — flows through this single queue, two clips can
 // never overlap. A toast is surfaced for announcement events to preserve the
 // notification UX that the old standalone announcement poller provided.
 useEffect(() => {
 if (radioMuted || radioCurrent || radioQueue.length === 0) return;
 const next = radioQueue[0];
 setRadioQueue(q => q.slice(1));
 setRadioCurrent(next);
 if (next.type === 'announcement') {
 notify(`📢 ${next.title}${next.message ? ': ' + next.message : ''}`, 'info');
 }
 const finish = () => setRadioCurrent(null);
 const playTts = () => {
 try {
 if (!('speechSynthesis' in window)) { setTimeout(finish, 800); return; }
 // Cancel any stale/interrupted utterance so this is the only voice playing.
 try { window.speechSynthesis.cancel(); } catch {}
 const utter = new SpeechSynthesisUtterance(`${next.title}. ${next.message || ''}`);
 utter.volume = radioVolume; utter.rate = 0.98; utter.pitch = 1;
 utter.onend = finish; utter.onerror = finish;
 window.speechSynthesis.speak(utter);
 } catch { finish(); }
 };
 if (next.audio_url) {
 try {
 // Ensure no TTS is mid-flight when an audio clip starts.
 try { window.speechSynthesis?.cancel(); } catch {}
 const audio = new Audio(next.audio_url);
 audio.volume = radioVolume;
 radioAudioRef.current = audio;
 audio.onended = finish;
 audio.onerror = finish;
 audio.play().catch(() => playTts());
 } catch { playTts(); }
 } else {
 playTts();
 }
 }, [radioQueue, radioCurrent, radioMuted, radioVolume]);

 // ── Persist radio settings ─────────────────────────────────────────
 useEffect(() => {
 localStorage.setItem('radio_enabled', radioEnabled ? '1' : '0');
 localStorage.setItem('radio_muted', radioMuted ? '1' : '0');
 localStorage.setItem('radio_volume', String(radioVolume));
 }, [radioEnabled, radioMuted, radioVolume]);

 // Unlock browser autoplay on first user interaction (one-shot)
 useEffect(() => {
 const unlock = () => {
 if (radioUnlockedRef.current) return;
 radioUnlockedRef.current = true;
 try { const u = new SpeechSynthesisUtterance(' '); u.volume = 0; window.speechSynthesis?.speak(u); } catch {}
 window.removeEventListener('click', unlock);
 window.removeEventListener('keydown', unlock);
 };
 window.addEventListener('click', unlock, { once: true });
 window.addEventListener('keydown', unlock, { once: true });
 return () => { window.removeEventListener('click', unlock); window.removeEventListener('keydown', unlock); };
 }, []);

 useEffect(() => {
 if (showForm !== 'salesOrder') {
 warehouseAlertIssuedRef.current = false;
 return;
 }

 if (!accessibleWarehouses.length) {
 if (!warehouseAlertIssuedRef.current) {
 notify('No warehouse access is currently assigned to your profile. Contact an administrator.', 'error');
 warehouseAlertIssuedRef.current = true;
 }
 return;
 }

 if (!forms.salesOrder.warehouse_id && !editingItem) {
 setForms((prev) => ({
 ...prev,
 salesOrder: {
 ...prev.salesOrder,
 warehouse_id: accessibleWarehouses[0].id,
 },
 }));
 }
 }, [showForm, accessibleWarehouses, forms.salesOrder.warehouse_id, editingItem]);

 async function fetchUpcomingBirthdays() {
 try {
 const res = await authedFetch('/api/staff/birthdays/upcoming?days_ahead=365');
 if (res.ok) {
 const result = await res.json();
 // Backend returns {success, count, birthdays: [...]} - extract the array
 const birthdayList = Array.isArray(result) ? result : (result.birthdays || []);
 setUpcomingBirthdays(birthdayList);
 }
 } catch (e) {
 console.error('Error fetching birthdays:', e);
 }
 }

 async function fetchAllData() {
 try {
 setLoading(true);
 const token = localStorage.getItem('access_token');
 const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
 
 const endpoints = {
 staff: '/api/staff/staffs?limit=1000',
 products: '/api/products/?size=1000',
 rawMaterials: '/api/raw-materials/?size=1000',
 stock: '/api/stock/levels',
 warehouses: '/api/warehouses/?size=1000',
 production: '/api/production/orders?limit=1000',
 sales: '/api/sales/orders?limit=1000',
 attendance: '/api/attendance/',
 customers: '/api/sales/customers?limit=1000&active_only=false',
 users: '/api/auth/users',
 };

 const entries = await Promise.all(
 Object.entries(endpoints).map(async ([key, url]) => {
 try {
 const res = await authedFetch(url, { headers });
 const items = await extractItems(res);
 console.log(`Fetched ${key}:`, items.length, 'items');
 if (key === 'warehouses') {
 console.log('WAREHOUSES DATA:', items);
 }
 return [key, Array.isArray(items) ? items : []];
 } catch (e) {
 console.error(`Error fetching ${key}:`, e);
 return [key, []];
 }
 })
 );

 setData(Object.fromEntries(entries));
 } catch (e) {
 notify(`Error loading data: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 async function fetchData(moduleKey) {
 const map = {
 staff: '/api/staff/staffs?limit=1000',
 products: '/api/products/?size=1000',
 rawMaterials: '/api/raw-materials/?size=1000',
 stock: '/api/stock/levels',
 warehouses: '/api/warehouses/?size=1000',
 production: '/api/production/orders?limit=1000',
 sales: '/api/sales/orders?limit=1000',
 attendance: '/api/attendance/',
 customers: '/api/sales/customers?limit=1000&active_only=false',
 users: '/api/auth/users',
 };
 try {
 setLoading(true);
 const token = localStorage.getItem('access_token');
 const headers = token ? { Authorization: `Bearer ${token}` } : {};
 const res = await authedFetch(map[moduleKey], { headers });
 const items = await extractItems(res);
 setData((prev) => ({ ...prev, [moduleKey]: Array.isArray(items) ? items : [] }));
 } catch (e) {
 notify(`Error fetching ${moduleKey}: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // Fetch module access for current user on mount
 useEffect(() => {
 if (currentUser && currentUser.id) {
 // Use module_access from login response if available
 if (currentUser.module_access) {
 setMyModuleAccess(currentUser.module_access);
 } else {
 authedFetch(`/api/auth/modules/access/${currentUser.id}`)
 .then(r => r.json())
 .then(d => setMyModuleAccess(d.modules || null))
 .catch(() => {});
 }
 }
 }, [currentUser]);

 // Fetch all users module access for admin settings
 async function fetchModuleAccessAll() {
 setMaLoading(true);
 try {
 const r = await authedFetch('/api/auth/modules/access-all');
 const d = await r.json();
 setModuleAccessData(Array.isArray(d) ? d : []);
 } catch(e) { console.error(e); }
 finally { setMaLoading(false); }
 }

 // Save module access for one user
 async function saveModuleAccess(userId, modules) {
 setMaSaving(prev => ({...prev, [userId]: true}));
 try {
 const r = await authedFetch(`/api/auth/modules/access/${userId}`, {
 method: 'PUT',
 headers: {'Content-Type': 'application/json'},
 body: JSON.stringify({ modules })
 });
 const d = await r.json();
 if (!r.ok) throw new Error(d.detail || 'Failed');
 notify(d.message, 'success');
 } catch(e) { notify(e.message, 'error'); }
 finally { setMaSaving(prev => ({...prev, [userId]: false})); }
 }

 // Fetch warehouse access for all users (admin settings)
 async function fetchWarehouseAccessAll() {
 setWaLoading(true);
 try {
 const r = await authedFetch('/api/auth/warehouse-access');
 const d = await r.json();
 setWarehouseAccessData(d && d.warehouses ? d : { warehouses: [], users: [] });
 } catch(e) { console.error(e); }
 finally { setWaLoading(false); }
 }

 // Save warehouse access for one user
 async function saveWarehouseAccess(userId, warehouseAccess) {
 setWaSaving(prev => ({...prev, [userId]: true}));
 try {
 const r = await authedFetch(`/api/auth/warehouse-access/${userId}`, {
 method: 'PUT',
 headers: {'Content-Type': 'application/json'},
 body: JSON.stringify({ warehouse_access: warehouseAccess })
 });
 const d = await r.json();
 if (!r.ok) throw new Error(d.detail || 'Failed');
 notify(d.message, 'success');
 } catch(e) { notify(e.message, 'error'); }
 finally { setWaSaving(prev => ({...prev, [userId]: false})); }
 }

 // ===== TRANSFER FETCHERS =====
 async function fetchTransfers() {
 try { const r = await authedFetch('/api/transfers/'); const d = await r.json(); setTransfersList(Array.isArray(d) ? d : []); }
 catch(e) { console.error('fetchTransfers error', e); }
 }
 async function fetchTransferSummary() {
 try { const r = await authedFetch('/api/transfers/summary'); const d = await r.json(); setTransferSummary(d); }
 catch(e) { console.error('fetchTransferSummary error', e); }
 }

 // ===== RETURNS FETCHERS =====
 async function fetchReturns() {
 try { const r = await authedFetch('/api/returns/'); const d = await r.json(); setReturnsList(Array.isArray(d) ? d : []); }
 catch(e) { console.error('fetchReturns error', e); }
 }
 async function fetchReturnsSummary() {
 try { const r = await authedFetch('/api/returns/summary'); const d = await r.json(); setReturnsSummary(d); }
 catch(e) { console.error('fetchReturnsSummary error', e); }
 }

 // ===== DAMAGED TRANSFERS FETCHERS =====
 async function fetchDamagedTransfers() {
 try { const r = await authedFetch('/api/damaged-transfers/'); const d = await r.json(); setDamagedTransfersList(Array.isArray(d) ? d : []); }
 catch(e) { console.error('fetchDamagedTransfers error', e); }
 }
 async function fetchDamagedTransfersSummary() {
 try { const r = await authedFetch('/api/damaged-transfers/summary'); const d = await r.json(); setDamagedTransfersSummary(d); }
 catch(e) { console.error('fetchDamagedTransfersSummary error', e); }
 }

 // ===== RECEIVE TRANSFERS FETCHERS =====
 async function fetchReceiveTransfers() {
 try { const r = await authedFetch('/api/receive-transfers/'); const d = await r.json(); setReceiveTransfersList(Array.isArray(d) ? d : []); }
 catch(e) { console.error('fetchReceiveTransfers error', e); }
 }
 async function fetchReceiveTransfersSummary() {
 try { const r = await authedFetch('/api/receive-transfers/summary'); const d = await r.json(); setReceiveTransfersSummary(d); }
 catch(e) { console.error('fetchReceiveTransfersSummary error', e); }
 }

 // ===== MARKETING FETCHERS =====
 async function fetchMktDashboard() { try { const r = await authedFetch('/api/marketing/dashboard'); setMktDashboard(await r.json()); } catch(e) { console.error(e); }}
 async function fetchMktPlans() { try { const r = await authedFetch('/api/marketing/plans'); const d = await r.json(); setMktPlans(d.items||[]); } catch(e) { console.error(e); }}
 async function fetchMktLogs() { try { const r = await authedFetch('/api/marketing/logs'); const d = await r.json(); setMktLogs(d.items||[]); } catch(e) { console.error(e); }}
 async function fetchMktProposals() { try { const r = await authedFetch('/api/marketing/proposals'); const d = await r.json(); setMktProposals(d.items||[]); } catch(e) { console.error(e); }}
 async function fetchMktProducts() { try { const r = await authedFetch('/api/marketing/products-catalog'); const d = await r.json(); setMktProductsCatalog(d.items||[]); } catch(e) { console.error(e); }}
 async function fetchMktFacilities() { try { const r = await authedFetch('/api/marketing/facilities'); const d = await r.json(); setMktFacilities(d.items||[]); } catch(e) { console.error(e); }}
 async function fetchMktPerformance() { try { const qs = new URLSearchParams(); qs.set('days', mktPerfDays); if (mktPerfMarketer) qs.set('marketer_staff_id', mktPerfMarketer); const r = await authedFetch('/api/marketing/performance?' + qs.toString()); setMktPerformance(await r.json()); } catch(e) { console.error(e); }}
 async function saveMktFacility(e) { e.preventDefault(); try { const url = editingMktFacility ? `/api/marketing/facilities/${editingMktFacility.id}` : '/api/marketing/facilities'; const method = editingMktFacility ? 'PUT' : 'POST'; const r = await authedFetch(url, { method, headers:{'Content-Type':'application/json'}, body: JSON.stringify(mktFacilityForm) }); if (!r.ok) throw new Error((await r.json()).detail || 'Save failed'); notify(`Facility ${editingMktFacility?'updated':'added'}`,'success'); setEditingMktFacility(null); setMktFacilityForm({ marketer_staff_id:'', facility_name:'', facility_type:'hospital', address:'', city:'', state:'', contact_person:'', contact_title:'', contact_phone:'', contact_email:'', secondary_contact:'', secondary_phone:'', relationship_status:'prospect', products_of_interest:'', last_visit_date:'', next_visit_date:'', visit_frequency:'monthly', estimated_monthly_value:'0', notes:'' }); fetchMktFacilities(); } catch(err) { notify(`Error: ${err.message}`,'error'); }}
 async function deleteMktFacility(id) { if (!window.confirm('Delete this facility record?')) return; try { await authedFetch(`/api/marketing/facilities/${id}`,{method:'DELETE'}); notify('Facility deleted','success'); fetchMktFacilities(); } catch(e) { notify(`Error: ${e.message}`,'error'); }}

 async function saveMktPlan(e) {
 e.preventDefault();
 try {
 setLoading(true);
 const url = editingMktPlan ? `/api/marketing/plans/${editingMktPlan.id}` : '/api/marketing/plans';
 const method = editingMktPlan ? 'PUT' : 'POST';
 const res = await authedFetch(url, { method, headers:{'Content-Type':'application/json'}, body: JSON.stringify(mktPlanForm) });
 if (!res.ok) throw new Error((await res.json()).detail||'Failed');
 notify(editingMktPlan ? 'Plan updated' : 'Weekly plan submitted', 'success');
 setMktPlanForm({ marketer_staff_id:'', week_start:'', week_end:'', title:'', objectives:'', target_areas:'', target_customers:'', planned_visits:'', planned_calls:'', budget_requested:'', status:'submitted' });
 setEditingMktPlan(null); fetchMktPlans(); fetchMktDashboard();
 } catch(e) { notify(`Error: ${e.message}`, 'error'); } finally { setLoading(false); }
 }
 async function deleteMktPlan(id) { if (!window.confirm('Delete this plan?')) return; try { await authedFetch(`/api/marketing/plans/${id}`,{method:'DELETE'}); notify('Plan deleted','success'); fetchMktPlans(); } catch(e) { notify(`Error: ${e.message}`,'error'); }}

 async function saveMktLog(e) {
 e.preventDefault();
 try {
 setLoading(true);
 const url = editingMktLog ? `/api/marketing/logs/${editingMktLog.id}` : '/api/marketing/logs';
 const method = editingMktLog ? 'PUT' : 'POST';
 const res = await authedFetch(url, { method, headers:{'Content-Type':'application/json'}, body: JSON.stringify(mktLogForm) });
 if (!res.ok) throw new Error((await res.json()).detail||'Failed');
 notify(editingMktLog ? 'Log updated' : 'Daily log submitted', 'success');
 setMktLogForm({ marketer_staff_id:'', log_date:new Date().toISOString().split('T')[0], start_time:'', end_time:'', location_visited:'', customer_contacted:'', contact_type:'visit', objective:'', activities_performed:'', outcome:'', products_discussed:'', samples_distributed:'0', orders_generated:'0', order_value:'0', challenges:'', follow_up_required:false, follow_up_date:'', follow_up_notes:'', mood_rating:'3' });
 setEditingMktLog(null); fetchMktLogs(); fetchMktDashboard();
 } catch(e) { notify(`Error: ${e.message}`, 'error'); } finally { setLoading(false); }
 }
 async function deleteMktLog(id) { if (!window.confirm('Delete this log?')) return; try { await authedFetch(`/api/marketing/logs/${id}`,{method:'DELETE'}); notify('Log deleted','success'); fetchMktLogs(); } catch(e) { notify(`Error: ${e.message}`,'error'); }}

 async function saveMktProposal(e) {
 e.preventDefault();
 try {
 setLoading(true);
 const url = editingMktProposal ? `/api/marketing/proposals/${editingMktProposal.id}` : '/api/marketing/proposals';
 const method = editingMktProposal ? 'PUT' : 'POST';
 const res = await authedFetch(url, { method, headers:{'Content-Type':'application/json'}, body: JSON.stringify(mktProposalForm) });
 if (!res.ok) throw new Error((await res.json()).detail||'Failed');
 notify(editingMktProposal ? 'Proposal updated' : 'Proposal submitted', 'success');
 setMktProposalForm({ marketer_staff_id:'', title:'', proposal_type:'campaign', target_audience:'', description:'', strategy:'', expected_outcome:'', budget_estimate:'', timeline_start:'', timeline_end:'', products_involved:'', channels:'', kpi_metrics:'', status:'draft' });
 setEditingMktProposal(null); fetchMktProposals(); fetchMktDashboard();
 } catch(e) { notify(`Error: ${e.message}`, 'error'); } finally { setLoading(false); }
 }
 async function deleteMktProposal(id) { if (!window.confirm('Delete this proposal?')) return; try { await authedFetch(`/api/marketing/proposals/${id}`,{method:'DELETE'}); notify('Proposal deleted','success'); fetchMktProposals(); } catch(e) { notify(`Error: ${e.message}`,'error'); }}

 // ===== HR / CUSTOMER CARE FETCHERS =====
 async function fetchHrDashboard() { try { const r = await authedFetch('/api/hr-customercare/dashboard'); setHrDashboard(await r.json()); } catch(e) { console.error(e); }}
 async function fetchHrStaff() { try { const r = await authedFetch('/api/hr-customercare/staff'); const d = await r.json(); setHrStaffList(d.items||[]); } catch(e) { console.error(e); }}
 async function fetchHrPerformance(days) { try { const r = await authedFetch(`/api/hr-customercare/staff-performance?days=${days||hrPerfDays}`); const d = await r.json(); setHrPerformance(d.items||[]); } catch(e) { console.error(e); }}
 async function fetchHrAttendance() { try { const r = await authedFetch('/api/hr-customercare/attendance-log?days=7'); const d = await r.json(); setHrAttendanceLog(d.items||[]); } catch(e) { console.error(e); }}
 async function fetchHrProducts() { try { const r = await authedFetch('/api/hr-customercare/products-catalog'); const d = await r.json(); setHrProductsCatalog(d.items||[]); } catch(e) { console.error(e); }}
 async function fetchHrSalesOrders() { try { const r = await authedFetch('/api/hr-customercare/sales-orders'); const d = await r.json(); setHrSalesOrders(d.items||[]); } catch(e) { console.error(e); }}
 async function fetchHrCustomers() { try { const r = await authedFetch('/api/hr-customercare/customers'); const d = await r.json(); setHrCustomers(d.items||[]); } catch(e) { console.error(e); }}

 // ===== CUSTOMER (WEB) ORDERS FETCHERS =====
 async function fetchCustomerOrders() {
 try {
 setCustomerOrdersLoading(true);
 const r = await authedFetch('/api/public/admin/orders?limit=500');
 if (!r.ok) throw new Error(`HTTP ${r.status}`);
 const d = await r.json();
 setCustomerOrders(d.items || []);
 } catch (e) {
 console.error('fetchCustomerOrders failed', e);
 notify(`Customer orders: ${e.message}`, 'error');
 } finally {
 setCustomerOrdersLoading(false);
 }
 }
 async function fetchCustomerOrderLines(orderId) {
 if (!orderId) return;
 try {
 const r = await authedFetch(`/api/public/admin/orders/${orderId}/lines`);
 if (!r.ok) throw new Error(`HTTP ${r.status}`);
 const d = await r.json();
 setCustomerOrderLines(prev => ({ ...prev, [orderId]: d.items || [] }));
 } catch (e) {
 console.error('fetchCustomerOrderLines failed', e);
 notify(`Order lines: ${e.message}`, 'error');
 }
 }
 function cleanPhoneForLink(phone) {
 if (!phone) return '';
 return String(phone).replace(/[^0-9+]/g, '');
 }
 async function confirmCustomerOrderToSales(webOrder) {
 // Make sure we have lines loaded
 let lines = customerOrderLines[webOrder.id];
 if (!lines) {
 await fetchCustomerOrderLines(webOrder.id);
 lines = customerOrderLines[webOrder.id] || [];
 }
 try {
 // Pre-fill the Sales Order form
 setForms((p) => ({
 ...p,
 salesOrder: {
 customer_id: webOrder.customer_id || '',
 warehouse_id: (accessibleWarehouses && accessibleWarehouses[0]) ? accessibleWarehouses[0].id : '',
 required_date: '',
 notes: `Confirmed from web order ${webOrder.order_number}`,
 payment_status: webOrder.payment_status || 'unpaid',
 order_type: webOrder.customer_type === 'wholesale' ? 'wholesale' : 'retail',
 lines: (lines || []).map(l => ({
 product_id: l.product_id || '',
 unit: l.unit || '',
 quantity: l.quantity || '',
 unit_price: l.unit_price || '',
 })),
 _customerSearch: webOrder.customer_name || '',
 }
 }));
 } catch (e) {
 console.warn('Pre-fill sales form failed', e);
 }
 setActiveModule('sales');
 try { openForm('salesOrder'); } catch(_) {}
 notify(`Loaded web order ${webOrder.order_number}. Review & save to confirm.`, 'success');
 }

 // ===================== PAYMENT TRACKING =====================
 async function fetchPtReconciliation() { try { const r = await authedFetch('/api/payment-tracking/reconciliation'); if(r.ok) setPtReconciliation(await r.json()); } catch(e) { console.error(e); }}
 async function fetchPtInvoices() { try { const r = await authedFetch('/api/payment-tracking/invoices'); if(r.ok) { const d = await r.json(); setPtInvoices(d.invoices||[]); } } catch(e) { console.error(e); }}
 async function fetchPtDebtors() { try { const r = await authedFetch('/api/payment-tracking/debtors'); if(r.ok) { const d = await r.json(); setPtDebtors(d.debtors||[]); } } catch(e) { console.error(e); }}
 async function fetchPtReminders() { try { const r = await authedFetch('/api/payment-tracking/reminders'); if(r.ok) { const d = await r.json(); setPtReminders(d.reminders||[]); } } catch(e) { console.error(e); }}
 async function fetchPtInvoiceDetail(invoiceId) {
 try {
 const r = await authedFetch(`/api/payment-tracking/invoices/${invoiceId}`);
 if(r.ok) {
 const data = await r.json();
 // Flatten: spread invoice fields to top level so ptSelectedInvoice.id, .status, etc. work
 const flat = { ...(data.invoice || {}), customer: data.customer, order: data.order, lines: data.lines || [], payments: data.payments || [] };
 setPtSelectedInvoice(flat);
 }
 } catch(e) { console.error(e); }
 }
 async function fetchPtDebtorDetail(customerId) {
 try { const r = await authedFetch(`/api/payment-tracking/debtors/${customerId}`); if(r.ok) setPtSelectedDebtor(await r.json()); } catch(e) { console.error(e); }
 }
 async function createInvoiceFromOrder(orderId) {
 try {
 setLoading(true);
 const r = await authedFetch(`/api/payment-tracking/invoices/from-order/${orderId}`, { method: 'POST' });
 const d = await r.json();
 if(!r.ok) throw new Error(d.detail || 'Failed');
 notify(`Invoice ${d.invoice_number} created successfully!`, 'success');
 fetchPtInvoices(); fetchPtReconciliation(); fetchPtDebtors();
 } catch(e) { notify(`Error: ${e.message}`, 'error'); } finally { setLoading(false); }
 }
 async function recordPtPayment(invoiceId) {
 if(!invoiceId) { notify('No invoice selected. Please select an invoice first.', 'error'); return; }
 try {
 setLoading(true);
 const payload = { ...ptPaymentForm, amount: parseFloat(ptPaymentForm.amount) };
 if(!payload.amount || payload.amount <= 0) { notify('Enter a valid amount', 'error'); return; }
 const r = await authedFetch(`/api/payment-tracking/invoices/${invoiceId}/payments`, {
 method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
 });
 const d = await r.json();
 if(!r.ok) throw new Error(d.detail || 'Failed');
 notify(`Payment of ${formatCurrency(payload.amount)} recorded!`, 'success');
 setPtPaymentForm({ amount: '', payment_method: 'bank_transfer', payment_date: new Date().toISOString().split('T')[0], reference: '', notes: '' });
 fetchPtInvoiceDetail(invoiceId); fetchPtInvoices(); fetchPtReconciliation(); fetchPtDebtors(); fetchPtReminders();
 } catch(e) { notify(`Error: ${e.message}`, 'error'); } finally { setLoading(false); }
 }
 async function deletePtPayment(paymentId, invoiceId) {
 if(!window.confirm('Delete this payment? This will recalculate the invoice balance.')) return;
 try {
 setLoading(true);
 const r = await authedFetch(`/api/payment-tracking/payments/${paymentId}`, { method: 'DELETE' });
 if(!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Failed'); }
 notify('Payment deleted', 'success');
 if(invoiceId) fetchPtInvoiceDetail(invoiceId);
 fetchPtInvoices(); fetchPtReconciliation(); fetchPtDebtors(); fetchPtReminders();
 } catch(e) { notify(`Error: ${e.message}`, 'error'); } finally { setLoading(false); }
 }
 async function fetchPtReminderMsg(customerId) {
 try { const r = await authedFetch(`/api/payment-tracking/debtors/${customerId}/reminder`); if(r.ok) setPtReminderMsg(await r.json()); } catch(e) { console.error(e); }
 }

 // ===================== LEGACY / PREVIOUS DEBTS =====================
 async function fetchLegacyDebts() { try { const r = await authedFetch('/api/legacy-debts/'); if(r.ok) { const d = await r.json(); setLegacyDebts(d.debts||[]); setLegacyDebtsSummary(d.summary||null); } } catch(e) { console.error(e); }}
 async function fetchLegacyDebtDetail(debtId) { try { const r = await authedFetch(`/api/legacy-debts/${debtId}`); if(r.ok) setLegacyDebtDetail(await r.json()); } catch(e) { console.error(e); }}
 async function createLegacyDebt() {
 try {
 setLoading(true);
 const payload = { ...legacyDebtForm, original_amount: parseFloat(legacyDebtForm.original_amount), amount_already_paid: parseFloat(legacyDebtForm.amount_already_paid || '0') };
 if(!payload.customer_id || !payload.description || !payload.original_amount || !payload.debt_date) { notify('Please fill in customer, description, amount and debt date', 'error'); return; }
 const token = localStorage.getItem('token');
 const r = await authedFetch('/api/legacy-debts/', {
 method: 'POST', headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) }, body: JSON.stringify(payload)
 });
 const d = await r.json();
 if(!r.ok) throw new Error(d.detail || 'Failed');
 notify(`Previous debt ${d.debt_number} recorded successfully!`, 'success');
 setLegacyDebtForm({ customer_id: '', description: '', original_amount: '', amount_already_paid: '0', debt_date: '', due_date: '', notes: '' });
 fetchLegacyDebts(); fetchPtDebtors(); fetchPtReconciliation();
 } catch(e) { notify(`Error: ${e.message}`, 'error'); } finally { setLoading(false); }
 }
 async function recordLegacyPayment(debtId) {
 try {
 setLoading(true);
 const payload = { ...legacyPaymentForm, amount: parseFloat(legacyPaymentForm.amount) };
 if(!payload.amount || payload.amount <= 0) { notify('Enter a valid amount', 'error'); return; }
 const r = await authedFetch(`/api/legacy-debts/${debtId}/payments`, {
 method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
 });
 const d = await r.json();
 if(!r.ok) throw new Error(d.detail || 'Failed');
 notify(`Payment of ${formatCurrency(payload.amount)} recorded!`, 'success');
 setLegacyPaymentForm({ amount: '', payment_method: 'bank_transfer', payment_date: new Date().toISOString().split('T')[0], reference: '', notes: '' });
 fetchLegacyDebtDetail(debtId); fetchLegacyDebts(); fetchPtDebtors(); fetchPtReconciliation();
 } catch(e) { notify(`Error: ${e.message}`, 'error'); } finally { setLoading(false); }
 }
 async function deleteLegacyPayment(paymentId, debtId) {
 if(!window.confirm('Delete this payment? This will recalculate the debt balance.')) return;
 try {
 setLoading(true);
 const r = await authedFetch(`/api/legacy-debts/payments/${paymentId}`, { method: 'DELETE' });
 if(!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Failed'); }
 notify('Payment deleted', 'success');
 if(debtId) fetchLegacyDebtDetail(debtId);
 fetchLegacyDebts(); fetchPtDebtors(); fetchPtReconciliation();
 } catch(e) { notify(`Error: ${e.message}`, 'error'); } finally { setLoading(false); }
 }
 async function deleteLegacyDebt(debtId) {
 if(!window.confirm('Delete this previous debt and all its payments? This cannot be undone.')) return;
 try {
 setLoading(true);
 const r = await authedFetch(`/api/legacy-debts/${debtId}`, { method: 'DELETE' });
 if(!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Failed'); }
 notify('Previous debt deleted', 'success');
 setPtView('legacyDebts'); setLegacyDebtDetail(null);
 fetchLegacyDebts(); fetchPtDebtors(); fetchPtReconciliation();
 } catch(e) { notify(`Error: ${e.message}`, 'error'); } finally { setLoading(false); }
 }

 // ===================== PROCUREMENT MODULE =====================
 async function fetchProcDashboard() { try { const r = await authedFetch('/api/procurement/dashboard'); if(r.ok) setProcDashboard(await r.json()); } catch(e) { console.error(e); }}
 async function fetchProcRequests(status) { try { const url = status ? `/api/procurement/requests?status=${status}` : '/api/procurement/requests'; const r = await authedFetch(url); if(r.ok) { const d = await r.json(); setProcRequests(d.items||[]); } } catch(e) { console.error(e); }}
 async function fetchProcOrders(status) { try { const url = status ? `/api/procurement/orders?status=${status}` : '/api/procurement/orders'; const r = await authedFetch(url); if(r.ok) { const d = await r.json(); setProcOrders(d.items||[]); } } catch(e) { console.error(e); }}
 async function fetchProcInvoices() { try { const r = await authedFetch('/api/procurement/invoices'); if(r.ok) { const d = await r.json(); setProcInvoices(d.items||[]); } } catch(e) { console.error(e); }}
 async function fetchProcExpenses(cat) { try { const url = cat ? `/api/procurement/expenses?category=${cat}` : '/api/procurement/expenses'; const r = await authedFetch(url); if(r.ok) { const d = await r.json(); setProcExpenses(d.items||[]); } } catch(e) { console.error(e); }}
 async function fetchProcRequestDetail(id) { try { const r = await authedFetch(`/api/procurement/requests/${id}`); if(r.ok) setProcSelectedRequest(await r.json()); } catch(e) { console.error(e); }}
 async function fetchProcOrderDetail(id) { try { const r = await authedFetch(`/api/procurement/orders/${id}`); if(r.ok) setProcSelectedOrder(await r.json()); } catch(e) { console.error(e); }}

 async function submitProcRequest(e) {
 e.preventDefault();
 try {
 setLoading(true);
 const payload = { ...procRequestForm, items: procRequestForm.items.filter(i => i.item_name) };
 const r = await authedFetch('/api/procurement/requests', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
 const d = await r.json();
 if(!r.ok) throw new Error(d.detail || 'Failed');
 notify(`Purchase request ${d.request_number} created!`, 'success');
 setProcRequestForm({ requested_by: '', department: '', category: 'general', priority: 'normal', title: '', description: '', justification: '', vendor_name: '', vendor_contact: '', vendor_phone: '', vendor_email: '', expected_delivery_date: '', notes: '', items: [{ item_type: 'general', item_name: '', quantity: '1', unit: 'each', estimated_unit_cost: '', specification: '' }] });
 setProcView('requests');
 fetchProcRequests(); fetchProcDashboard();
 } catch(e) { notify(`Error: ${e.message}`, 'error'); } finally { setLoading(false); }
 }

 async function approveProcRequest(id) {
 try { setLoading(true);
 const r = await authedFetch(`/api/procurement/requests/${id}/approve`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ approved_by: 'Admin' }) });
 if(!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Failed'); }
 notify('Request approved!', 'success');
 fetchProcRequests(); fetchProcDashboard(); if(procSelectedRequest) fetchProcRequestDetail(id);
 } catch(e) { notify(e.message, 'error'); } finally { setLoading(false); }
 }

 async function rejectProcRequest(id) {
 const reason = window.prompt('Rejection reason:');
 if(reason === null) return;
 try { setLoading(true);
 const r = await authedFetch(`/api/procurement/requests/${id}/reject`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rejected_by: 'Admin', reason }) });
 if(!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Failed'); }
 notify('Request rejected', 'success');
 fetchProcRequests(); fetchProcDashboard(); if(procSelectedRequest) fetchProcRequestDetail(id);
 } catch(e) { notify(e.message, 'error'); } finally { setLoading(false); }
 }

 async function submitProcOrder(e) {
 e.preventDefault();
 try {
 setLoading(true);
 const payload = { ...procOrderForm, items: procOrderForm.items.filter(i => i.item_name) };
 const r = await authedFetch('/api/procurement/orders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
 const d = await r.json();
 if(!r.ok) throw new Error(d.detail || 'Failed');
 notify(`Purchase order ${d.po_number} created! Total: ${formatCurrency(d.total_amount)}`, 'success');
 setProcOrderForm({ vendor_name: '', vendor_contact: '', vendor_phone: '', vendor_email: '', vendor_address: '', expected_delivery: '', tax_amount: '0', shipping_cost: '0', notes: '', created_by: '', request_id: '', items: [{ item_type: 'general', item_name: '', quantity: '1', unit: 'each', unit_cost: '', specification: '' }] });
 setProcView('orders');
 fetchProcOrders(); fetchProcDashboard();
 } catch(e) { notify(`Error: ${e.message}`, 'error'); } finally { setLoading(false); }
 }

 async function receiveProcOrder(id) {
 if(!window.confirm('Mark this PO as received?')) return;
 try { setLoading(true);
 const r = await authedFetch(`/api/procurement/orders/${id}/receive`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) });
 if(!r.ok) { const d = await r.json(); throw new Error(d.detail || 'Failed'); }
 notify('PO marked as received!', 'success');
 fetchProcOrders(); fetchProcDashboard(); if(procSelectedOrder) fetchProcOrderDetail(id);
 } catch(e) { notify(e.message, 'error'); } finally { setLoading(false); }
 }

 async function payProcOrder(id) {
 try { setLoading(true);
 const payload = { ...procPoPayForm, amount: parseFloat(procPoPayForm.amount) };
 if(!payload.amount || payload.amount <= 0) { notify('Enter a valid amount', 'error'); return; }
 const r = await authedFetch(`/api/procurement/orders/${id}/pay`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
 const d = await r.json();
 if(!r.ok) throw new Error(d.detail || 'Failed');
 notify(d.message, 'success');
 setProcPoPayForm({ amount: '', payment_method: '', payment_reference: '' });
 fetchProcOrders(); fetchProcDashboard(); if(procSelectedOrder) fetchProcOrderDetail(id);
 } catch(e) { notify(e.message, 'error'); } finally { setLoading(false); }
 }

 async function submitProcExpense(e) {
 e.preventDefault();
 try {
 setLoading(true);
 const payload = { ...procExpenseForm, amount: parseFloat(procExpenseForm.amount) };
 const r = await authedFetch('/api/procurement/expenses', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
 const d = await r.json();
 if(!r.ok) throw new Error(d.detail || 'Failed');
 notify(`Expense ${d.expense_number} recorded!`, 'success');
 setProcExpenseForm({ category: 'general', subcategory: '', description: '', amount: '', payment_method: '', payment_reference: '', payment_date: new Date().toISOString().split('T')[0], recipient: '', approved_by: '', notes: '' });
 fetchProcExpenses(); fetchProcDashboard();
 } catch(e) { notify(`Error: ${e.message}`, 'error'); } finally { setLoading(false); }
 }

 // ===================== LOGISTICS MODULE =====================
 async function fetchLogDashboard() { try { const r = await authedFetch('/api/logistics/dashboard'); if(r.ok) setLogDashboard(await r.json()); } catch(e) { console.error(e); }}
 async function fetchLogManifests() { try { const r = await authedFetch('/api/logistics/manifests'); if(r.ok) { const d = await r.json(); setLogManifests(d.items||[]); } } catch(e) { console.error(e); }}
 async function fetchLogAnalytics() { try { const r = await authedFetch('/api/logistics/analytics'); if(r.ok) setLogAnalytics(await r.json()); } catch(e) { console.error(e); }}
 async function fetchLogManifestDetail(id) { try { const r = await authedFetch(`/api/logistics/manifests/${id}`); if(r.ok) setLogSelectedManifest(await r.json()); } catch(e) { console.error(e); }}

 async function submitLogManifest(e) {
 e.preventDefault();
 try {
 setLoading(true);
 const payload = {
 ...logManifestForm,
 transport_cost: parseFloat(logManifestForm.transport_cost || 0),
 additional_charges: parseFloat(logManifestForm.additional_charges || 0),
 customers: logManifestForm.customers.filter(c => c.customer_name).map(c => ({
 ...c, items: c.items.filter(i => i.product_name)
 }))
 };
 const r = await authedFetch('/api/logistics/manifests', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
 const d = await r.json();
 if(!r.ok) throw new Error(d.detail || 'Failed');
 notify(`Manifest ${d.manifest_number} created with ${payload.customers.length} customer(s)! Cost: ${formatCurrency(d.total_cost)}`, 'success');
 setLogManifestForm({ logistics_officer: '', delivery_date: new Date().toISOString().split('T')[0], vehicle_details: '', driver_name: '', driver_phone: '', transport_mode: 'vehicle', transport_cost: '', additional_charges: '0', notes: '', customers: [{ customer_id: '', customer_name: '', customer_phone: '', delivery_address: '', city: '', state: '', items: [{ product_id: '', product_name: '', sku: '', quantity: '1', unit: 'each' }] }] });
 setLogView('manifests');
 fetchLogManifests(); fetchLogDashboard(); fetchLogAnalytics();
 } catch(e) { notify(`Error: ${e.message}`, 'error'); } finally { setLoading(false); }
 }

 async function confirmCustomerDelivery(mcId) {
 try {
 setLoading(true);
 const r = await authedFetch(`/api/logistics/manifests/customer/${mcId}/confirm`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(logConfirmForm) });
 const d = await r.json();
 if(!r.ok) throw new Error(d.detail || 'Failed');
 notify('Delivery confirmed for customer!', 'success');
 setLogConfirmingCustomerId(null);
 setLogConfirmForm({ receiver_name: '', receiver_phone: '', physical_invoice_number: '', delivery_notes: '', signature_collected: true });
 if(logSelectedManifest) fetchLogManifestDetail(logSelectedManifest.id);
 fetchLogDashboard();
 } catch(e) { notify(`Error: ${e.message}`, 'error'); } finally { setLoading(false); }
 }

 async function updateManifestStatus(manifestId, status) {
 try {
 const r = await authedFetch(`/api/logistics/manifests/${manifestId}/status`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }) });
 const d = await r.json();
 if(!r.ok) throw new Error(d.detail || 'Failed');
 notify(d.message, 'success');
 fetchLogManifests(); fetchLogDashboard();
 if(logSelectedManifest) fetchLogManifestDetail(manifestId);
 } catch(e) { notify(`Error: ${e.message}`, 'error'); }
 }

 async function deleteManifest(manifestId, manifestNumber) {
 if (!window.confirm(`Are you sure you want to PERMANENTLY DELETE manifest ${manifestNumber}?\n\nThis action CANNOT be undone.`)) return;
 try {
 const r = await authedFetch(`/api/logistics/manifests/${manifestId}`, { method: 'DELETE' });
 const d = await r.json();
 if(!r.ok) throw new Error(d.detail || 'Delete failed');
 notify(d.message, 'success');
 setLogSelectedManifest(null);
 fetchLogManifests(); fetchLogDashboard();
 } catch(e) { notify(`Error: ${e.message}`, 'error'); }
 }

 async function approveMktPlan(planId, planTitle, budget) {
 if (!window.confirm(`Approve marketing plan "${planTitle}"?\n\nBudget of \u20A6${Number(budget).toLocaleString()} will be automatically added to expenses.`)) return;
 try {
 const token = localStorage.getItem('access_token');
 const headers = { 'Content-Type': 'application/json' };
 if (token) headers['Authorization'] = `Bearer ${token}`;
 const r = await authedFetch(`/api/marketing/plans/${planId}/approve`, {
 method: 'POST', headers,
 body: JSON.stringify({ budget_approved: budget, approved_by: currentUser?.full_name || 'Admin' })
 });
 const d = await r.json();
 if(!r.ok) throw new Error(d.detail || d.message || 'Approval failed');
 notify(d.message, 'success');
 fetchMktPlans(); fetchMktDashboard();
 } catch(e) { notify(`Error: ${e.message}`, 'error'); }
 }

 async function downloadManifestPdf(manifestId, manifestNumber) {
 try {
 notify('Generating PDF...', 'info');
 const res = await authedFetch(`/api/logistics/manifests/${manifestId}/generate-doc`, { method: 'POST' });
 if (!res.ok) {
 const errData = await res.json().catch(() => ({}));
 throw new Error(errData.detail || `Server returned ${res.status}`);
 }
 const blob = await res.blob();
 const url = window.URL.createObjectURL(blob);
 const a = document.createElement('a');
 a.href = url;
 a.download = `Manifest-${manifestNumber || manifestId}.pdf`;
 document.body.appendChild(a);
 a.click();
 a.remove();
 window.URL.revokeObjectURL(url);
 notify('Manifest PDF downloaded', 'success');
 } catch (e) {
 notify(`PDF error: ${e.message}`, 'error');
 }
 }

 // Thermal print functions (80mm, Georgia font, company logo)
 // These endpoints are authenticated, and window.open() cannot send an
 // Authorization header -- openAuthed fetches the document as a blob first.
 async function printManifestThermal(manifestId, manifestNumber) {
 try { await openAuthed(`/api/logistics/manifests/${manifestId}/thermal-print`); }
 catch (e) { notify(e.message, 'error'); }
 }

 async function printInvoiceThermal(orderId) {
 try { await openAuthed(`/api/sales/orders/${orderId}/thermal-invoice`); }
 catch (e) { notify(e.message, 'error'); }
 }

 async function printReceiptThermal(orderId) {
 try { await openAuthed(`/api/sales/orders/${orderId}/thermal-receipt`); }
 catch (e) { notify(e.message, 'error'); }
 }

 async function fetchFinancialData() {
 try {
 setLoading(true);
 const res = await authedFetch('/api/financial/company-status');
 if (!res.ok) throw new Error('Failed to fetch financial data');
 const result = await res.json();
 setFinancialData(result.data || {});
 } catch (e) {
 notify(`Error loading financial data: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 async function exportFinancialPDF() {
 try { await openAuthed('/api/financial/company-status/export'); }
 catch (e) { notify(`Export failed: ${e.message}`, 'error'); }
 }

 // ===================== SALARY & PAYROLL FUNCTIONS =====================
 async function fetchPayrollDashboard(start, end) {
 try {
 const s = start || payrollPeriod.start;
 const e = end || payrollPeriod.end;
 const res = await authedFetch(`/api/staff/payroll/dashboard?period_start=${s}&period_end=${e}`);
 if (!res.ok) throw new Error('Failed to fetch payroll dashboard');
 const result = await res.json();
 setPayrollDashboard(result);
 } catch (e) {
 console.error('Payroll dashboard error:', e);
 notify(`Error: ${e.message}`, 'error');
 }
 }

 async function fetchPayrollEntries() {
 try {
 const res = await authedFetch('/api/staff/payroll/entries');
 if (!res.ok) throw new Error('Failed to fetch payroll entries');
 const result = await res.json();
 setPayrollEntries(result.entries || []);
 } catch (e) {
 console.error('Payroll entries error:', e);
 }
 }

 async function bulkProcessPayroll() {
 if (!window.confirm(`Process payroll for ALL active staff for period ${payrollPeriod.start} to ${payrollPeriod.end}?`)) return;
 try {
 setPayrollProcessing(true);
 const res = await authedFetch(`/api/staff/payroll/bulk-calculate?period_start=${payrollPeriod.start}&period_end=${payrollPeriod.end}`, { method: 'POST' });
 if (!res.ok) throw new Error((await res.json()).detail || 'Bulk payroll failed');
 const result = await res.json();
 notify(`Payroll processed for ${result.processed_count} staff (${result.skipped_count} skipped)`, 'success');
 fetchPayrollDashboard();
 fetchPayrollEntries();
 } catch (e) {
 notify(`Payroll error: ${e.message}`, 'error');
 } finally {
 setPayrollProcessing(false);
 }
 }

 async function updatePayrollEntryStatus(payrollId, newStatus) {
 try {
 const res = await authedFetch(`/api/staff/payroll/entries/${payrollId}/status?new_status=${newStatus}`, { method: 'PUT' });
 if (!res.ok) throw new Error('Failed');
 notify(`Status updated to ${newStatus}`, 'success');
 fetchPayrollDashboard();
 fetchPayrollEntries();
 } catch (e) {
 notify(`Error: ${e.message}`, 'error');
 }
 }

 async function downloadPayslipPdf(payrollId, staffName) {
 try {
 const res = await authedFetch(`/api/staff/payslip/${payrollId}/pdf-v2`);
 if (!res.ok) throw new Error('Failed to generate payslip PDF');
 const blob = await res.blob();
 const url = window.URL.createObjectURL(blob);
 const a = document.createElement('a');
 a.href = url;
 a.download = `payslip_${staffName || payrollId}.pdf`;
 document.body.appendChild(a);
 a.click();
 a.remove();
 window.URL.revokeObjectURL(url);
 notify('Payslip downloaded', 'success');
 } catch (e) {
 notify(`Payslip error: ${e.message}`, 'error');
 }
 }

 async function processSinglePayroll(staffId) {
 try {
 setPayrollProcessing(true);
 const payload = { staff_id: staffId, pay_period_start: payrollPeriod.start, pay_period_end: payrollPeriod.end };
 const res = await authedFetch('/api/staff/payroll/calculate-v2', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
 if (!res.ok) throw new Error((await res.json()).detail || 'Failed');
 const payroll = await res.json();
 notify('Payroll calculated', 'success');
 // auto-download payslip
 await downloadPayslipPdf(payroll.id, '');
 fetchPayrollDashboard();
 fetchPayrollEntries();
 } catch (e) {
 notify(`Error: ${e.message}`, 'error');
 } finally {
 setPayrollProcessing(false);
 }
 }

 // ===================== PRODUCTION CONSUMABLES =====================
 async function fetchConsumables() {
 try {
 const res = await authedFetch('/api/production-consumables/');
 const items = await extractItems(res);
 setProdConsumables(Array.isArray(items) ? items : (items.items || []));
 } catch (e) { console.error('Error fetching consumables:', e); }
 }

 async function saveConsumable(e) {
 e.preventDefault();
 try {
 setLoading(true);
 const url = editingConsumable ? `/api/production-consumables/${editingConsumable.id}` : '/api/production-consumables/';
 const method = editingConsumable ? 'PUT' : 'POST';
 const res = await authedFetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(consumableForm) });
 if (!res.ok) throw new Error((await res.json()).detail || 'Failed');
 notify(editingConsumable ? 'Consumable updated' : 'Consumable added', 'success');
 setConsumableForm({ name: '', unit: 'unit', unit_cost: '', category: '', description: '', current_stock: '', reorder_level: '' });
 setEditingConsumable(null);
 fetchConsumables();
 fetchLowStockConsumables();
 } catch (e) { notify(`Error: ${e.message}`, 'error'); } finally { setLoading(false); }
 }

 async function deleteConsumable(id) {
 if (!window.confirm('Delete this consumable?')) return;
 try {
 const res = await authedFetch(`/api/production-consumables/${id}`, { method: 'DELETE' });
 if (!res.ok) throw new Error('Failed');
 notify('Consumable deleted', 'success');
 fetchConsumables();
 fetchLowStockConsumables();
 } catch (e) { notify(`Error: ${e.message}`, 'error'); }
 }

 async function fetchLowStockConsumables() {
 try {
 const res = await authedFetch('/api/production-consumables/low-stock');
 const data = await res.json();
 setLowStockConsumables(data.items || []);
 } catch (e) { console.error('Error fetching low stock:', e); }
 }

 async function restockConsumable() {
 if (!restockModal || !restockQty || parseFloat(restockQty) <= 0) return;
 try {
 setLoading(true);
 const res = await authedFetch(`/api/production-consumables/${restockModal.id}/restock`, {
 method: 'POST', headers: { 'Content-Type': 'application/json' },
 body: JSON.stringify({ quantity: parseFloat(restockQty) })
 });
 if (!res.ok) throw new Error((await res.json()).detail || 'Failed');
 const result = await res.json();
 notify(result.message, 'success');
 setRestockModal(null);
 setRestockQty('');
 fetchConsumables();
 fetchLowStockConsumables();
 } catch (e) { notify(`Error: ${e.message}`, 'error'); } finally { setLoading(false); }
 }

 // ===================== PRODUCTION COMPLETIONS =====================
 async function fetchProdCompletions() {
 try {
 const res = await authedFetch('/api/production-completions/');
 const result = await res.json();
 setProdCompletions(result.items || []);
 } catch (e) { console.error('Error fetching completions:', e); }
 }

 async function fetchDailyStaffSummary(dateStr) {
 try {
 const res = await authedFetch(`/api/production-completions/daily-staff-summary?production_date=${dateStr}`);
 if (!res.ok) return;
 const summary = await res.json();
 setProdStaffSummary(summary);
 setProdCompletionForm(prev => ({
 ...prev,
 staff_count: summary.staff_count,
 total_hours_worked: summary.total_hours_worked,
 total_wages_paid: summary.total_wages_paid
 }));
 } catch (e) { console.error('Error fetching staff summary:', e); }
 }

 async function fetchBomMaterials(productId, qty) {
 try {
 const res = await authedFetch(`/api/production-completions/product-bom-materials?product_id=${productId}&quantity=${qty || 1}`);
 if (!res.ok) return;
 const result = await res.json();
 setProdBomMaterials(result.materials || []);
 setProdCompletionForm(prev => ({
 ...prev,
 materials: (result.materials || []).map(m => ({
 raw_material_id: m.raw_material_id,
 quantity: m.total_qty
 }))
 }));
 } catch (e) { console.error('Error fetching BOM materials:', e); }
 }

 async function submitProdCompletion(e) {
 e.preventDefault();
 try {
 setLoading(true);
 const payload = { ...prodCompletionForm };
 // Attach consumable line items
 payload.consumables = (prodCompletionForm.consumables || []).filter(c => c.consumable_id && c.quantity > 0);
 payload.materials = (prodCompletionForm.materials || []).filter(m => m.raw_material_id && m.quantity > 0);
 const res = await authedFetch('/api/production-completions/', {
 method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
 });
 if (!res.ok) throw new Error((await res.json()).detail || 'Failed');
 const result = await res.json();
 notify(`Production recorded: ${result.data?.qty_produced || 0} units, Cost/Unit: ${formatCurrency(result.data?.cost_per_unit || 0)}`, 'success');
 // Reset form
 setProdCompletionForm({
 product_id: '', production_date: new Date().toISOString().split('T')[0],
 qty_produced: '', qty_damaged: '0', damage_notes: '',
 staff_count: 0, total_hours_worked: 0, total_wages_paid: 0,
 energy_cost: '', lunch_cost: '', warehouse_id: '', notes: '',
 consumables: [], materials: []
 });
 setProdStaffSummary(null);
 setProdBomMaterials([]);
 fetchProdCompletions();
 } catch (e) { notify(`Error: ${e.message}`, 'error'); } finally { setLoading(false); }
 }

 function addCompletionConsumable() {
 setProdCompletionForm(prev => ({
 ...prev,
 consumables: [...(prev.consumables || []), { consumable_id: '', quantity: '' }]
 }));
 }

 function updateCompletionConsumable(idx, field, val) {
 setProdCompletionForm(prev => {
 const arr = [...(prev.consumables || [])];
 arr[idx] = { ...arr[idx], [field]: val };
 return { ...prev, consumables: arr };
 });
 }

 function removeCompletionConsumable(idx) {
 setProdCompletionForm(prev => ({
 ...prev,
 consumables: prev.consumables.filter((_, i) => i !== idx)
 }));
 }

 // ===================== MACHINES & EQUIPMENT =====================
 async function fetchMachines() {
 try {
 const res = await authedFetch('/api/machines/');
 const items = await extractItems(res);
 setMachines(Array.isArray(items) ? items : (items.items || []));
 } catch (e) { console.error('Error fetching machines:', e); }
 }

 async function fetchMachinesDashboard() {
 try {
 const res = await authedFetch('/api/machines/dashboard/summary');
 if (!res.ok) return;
 const result = await res.json();
 setMachinesDashboard(result);
 } catch (e) { console.error('Error fetching machines dashboard:', e); }
 }

 async function saveMachine(e) {
 e.preventDefault();
 try {
 setLoading(true);
 const url = editingMachine ? `/api/machines/${editingMachine.id}` : '/api/machines/';
 const method = editingMachine ? 'PUT' : 'POST';
 const res = await authedFetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(machineForm) });
 if (!res.ok) throw new Error((await res.json()).detail || 'Failed');
 notify(editingMachine ? 'Machine updated' : 'Machine added', 'success');
 setMachineForm({ name: '', equipment_type: '', serial_number: '', model: '', manufacturer: '', purchase_date: '', purchase_cost: '', current_value: '', depreciation_rate: '10', depreciation_method: 'straight_line', location: '', status: 'Operational', notes: '' });
 setEditingMachine(null);
 setShowForm('');
 fetchMachines();
 fetchMachinesDashboard();
 } catch (e) { notify(`Error: ${e.message}`, 'error'); } finally { setLoading(false); }
 }

 async function deleteMachine(id) {
 if (!window.confirm('Delete this machine/equipment?')) return;
 try {
 const res = await authedFetch(`/api/machines/${id}`, { method: 'DELETE' });
 if (!res.ok) throw new Error('Failed');
 notify('Machine deleted', 'success');
 fetchMachines();
 fetchMachinesDashboard();
 } catch (e) { notify(`Error: ${e.message}`, 'error'); }
 }

 async function fetchMachineDetail(machineId) {
 try {
 const [maintRes, faultRes, depRes] = await Promise.all([
 authedFetch(`/api/machines/${machineId}/maintenance`),
 authedFetch(`/api/machines/${machineId}/faults`),
 authedFetch(`/api/machines/${machineId}/depreciation`)
 ]);
 if (maintRes.ok) { const d = await maintRes.json(); setMachineMaintenanceRecords(d.items || d || []); }
 if (faultRes.ok) { const d = await faultRes.json(); setMachineFaults(d.items || d || []); }
 if (depRes.ok) { const d = await depRes.json(); setMachineDepreciation(d); }
 } catch (e) { console.error('Error fetching machine detail:', e); }
 }

 async function saveMaintenanceRecord(e) {
 e.preventDefault();
 if (!selectedMachine) return;
 try {
 setLoading(true);
 const res = await authedFetch(`/api/machines/${selectedMachine.id}/maintenance`, {
 method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(maintenanceForm)
 });
 if (!res.ok) throw new Error((await res.json()).detail || 'Failed');
 notify('Maintenance record added', 'success');
 setMaintenanceForm({ maintenance_type: 'routine', scheduled_date: '', description: '', cost: '', performed_by: '', notes: '' });
 fetchMachineDetail(selectedMachine.id);
 } catch (e) { notify(`Error: ${e.message}`, 'error'); } finally { setLoading(false); }
 }

 async function saveFault(e) {
 e.preventDefault();
 if (!selectedMachine) return;
 try {
 setLoading(true);
 const res = await authedFetch(`/api/machines/${selectedMachine.id}/faults`, {
 method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(faultForm)
 });
 if (!res.ok) throw new Error((await res.json()).detail || 'Failed');
 notify('Fault recorded', 'success');
 setFaultForm({ fault_date: new Date().toISOString().split('T')[0], description: '', severity: 'Medium', status: 'Open', resolution: '', downtime_hours: '', repair_cost: '', reported_by: '' });
 fetchMachineDetail(selectedMachine.id);
 } catch (e) { notify(`Error: ${e.message}`, 'error'); } finally { setLoading(false); }
 }

 function openForm(formName, item = null) {
 setEditingItem(item);
 setShowForm(formName);
 if (formName === 'staff' && item) {
 setForms((p) => ({ ...p, staff: {
 first_name: item.first_name || '', last_name: item.last_name || '',
 phone: item.phone || '', date_of_birth: item.date_of_birth || '',
 position: item.position || '', payment_mode: item.payment_mode || '', 
 hourly_rate: item.hourly_rate || '', monthly_salary: item.monthly_salary || '',
 hire_date: item.hire_date || '', bank_name: item.bank_name || '',
 bank_account_number: item.bank_account_number || '', bank_account_name: item.bank_account_name || '',
 bank_currency: item.bank_currency || 'NGN'
 }}));
 }
 if (formName === 'product' && item) {
 setForms((p) => ({ ...p, product: { 
 sku: item.sku || '', name: item.name || '', unit: item.unit || 'each', 
 description: item.description || '', manufacturer: item.manufacturer || '',
 reorder_level: item.reorder_level || '',
 selling_price: item.selling_price || '', retail_price: item.retail_price || '', wholesale_price: item.wholesale_price || '',
 lead_time_days: item.lead_time_days || '',
 minimum_order_quantity: item.minimum_order_quantity || '1',
 pricing: (item.pricing && item.pricing.length > 0) ? item.pricing : [{ 
 unit: 'each',
 cost_price: item.cost_price || '',
 retail_price: item.retail_price || item.selling_price || '',
 wholesale_price: item.wholesale_price || item.retail_price || ''
 }]
 } }));
 }
 if (formName === 'rawMaterial' && item) {
 setForms((p) => ({ ...p, rawMaterial: { sku: item.sku || '', name: item.name || '', unit_cost: item.unit_cost || '' } }));
 }
 if (formName === 'warehouse' && item) {
 setForms((p) => ({ ...p, warehouse: { 
 code: item.code || '', 
 name: item.name || '', 
 location: item.location || '',
 manager_id: item.manager_id || ''
 } }));
 }
 if (formName === 'customer' && item) {
 setForms((p) => ({ ...p, customer: { 
 customer_code: item.customer_code || '', 
 name: item.name || '', 
 email: item.email || '', 
 phone: item.phone || '', 
 address: item.address || '', 
 credit_limit: item.credit_limit || '0' 
 } }));
 }
 if (formName === 'stockIntake' && item) {
 setForms((p) => ({ ...p, stockIntake: { ...p.stockIntake, ...item } }));
 }
 if (formName === 'rawMaterialIntake' && item) {
 setForms((p) => ({ ...p, rawMaterialIntake: { ...p.rawMaterialIntake, ...item } }));
 }
 if (formName === 'payroll' && item && item.staff_id) {
 setForms((p) => ({ ...p, payroll: { ...p.payroll, staff_id: item.staff_id } }));
 }
 if (formName === 'productionOrder' && item) {
 setForms((p) => ({ ...p, productionOrder: {
 product_id: item.product_id || '',
 quantity_planned: item.quantity_planned || item.quantity_to_produce || '',
 scheduled_start_date: item.scheduled_start_date || '',
 scheduled_end_date: item.scheduled_end_date || '',
 priority: item.priority ?? 5,
 notes: item.notes || ''
 }}));
 }
 if (formName === 'salesOrder' && item) {
 const allowedWarehouseIds = new Set(accessibleWarehouses.map((w) => w.id));
 const resolvedWarehouseId =
 (item.warehouse_id && (currentUser?.role === 'admin' || allowedWarehouseIds.has(item.warehouse_id)))
 ? item.warehouse_id
 : (accessibleWarehouses[0] ? accessibleWarehouses[0].id : '');
 if (!resolvedWarehouseId && item.warehouse_id && !allowedWarehouseIds.has(item.warehouse_id)) {
 notify('You do not have access to the original warehouse for this order. Please select one of your assigned warehouses.', 'warning');
 }
 setForms((p) => ({ ...p, salesOrder: {
 customer_id: item.customer_id || '',
 warehouse_id: resolvedWarehouseId,
 payment_status: item.payment_status || 'unpaid',
 order_type: item.order_type || 'retail',
 required_date: item.required_date ? (item.required_date.substring(0,10)) : '',
 notes: item.notes || '',
 // Preserve unit if it exists on saved lines
 lines: Array.isArray(item.lines) ? item.lines.map(l => ({ product_id: l.product_id || '', unit: l.unit || '', quantity: l.quantity || '', unit_price: l.unit_price || '' })) : []
 }}));
 }
 }

 function resetForm(formName) {
 const defaults = {
 staff: {
 first_name: '', last_name: '', phone: '', date_of_birth: '',
 position: '', payment_mode: '', hourly_rate: '', monthly_salary: '', hire_date: '',
 bank_name: '', bank_account_number: '', bank_account_name: '', bank_currency: 'NGN'
 },
 product: { 
 sku: '', name: '', unit: 'each', description: '', manufacturer: '',
 reorder_level: '', selling_price: '', 
 lead_time_days: '', minimum_order_quantity: '1'
 },
 rawMaterial: { sku: '', name: '', unit_cost: '' },
 stockIntake: { product_id: '', warehouse_id: '', quantity: '', unit_cost: '', unit: '', supplier: '', batch_number: '', expiry_date: '', notes: '' },
 rawMaterialIntake: { raw_material_id: '', warehouse_id: '', quantity: '', unit_cost: '', supplier: '', batch_number: '', expiry_date: '', notes: '' },
 warehouse: { code: '', name: '', location: '', manager_id: '' },
 customer: { customer_code: '', name: '', email: '', phone: '', address: '', credit_limit: '0' },
 payroll: { staff_id: '', pay_period_start: '', pay_period_end: '' },
 production: { product_id: '', quantity_to_produce: '' },
 productionOrder: { product_id: '', quantity_planned: '', scheduled_start_date: '', scheduled_end_date: '', priority: 5, notes: '' },
 salesOrder: { customer_id: '', warehouse_id: '', required_date: '', notes: '', payment_status: 'unpaid', order_type: 'retail', lines: [] },
 };
 if (accessibleWarehouses.length === 1) {
 defaults.salesOrder.warehouse_id = accessibleWarehouses[0].id;
 }
 setForms((p) => ({ ...p, [formName]: defaults[formName] }));
 }

 function handleFormChange(formName, field, value) {
 if (formName === 'rawMaterial' && field === 'name') {
 // Auto-generate SKU from raw material name
 const words = value.replace(/[^a-zA-Z0-9\s]/g, '').split(/\s+/).filter(w => w);
 let initials;
 if (words.length === 0) {
 initials = '';
 } else if (words.length === 1) {
 initials = words[0].substring(0, 3).toUpperCase();
 } else {
 initials = words.map(w => w[0].toUpperCase()).join('');
 }
 const autoSku = initials ? `RM-${initials}` : '';
 setForms((prev) => ({ ...prev, rawMaterial: { ...prev.rawMaterial, name: value, sku: autoSku } }));
 return;
 }
 if (formName === 'salesOrder' && field === 'order_type') {
 setForms((prev) => {
 const orderType = value || 'retail';
 const lines = prev.salesOrder.lines.map((line) => {
 if (!line || !line.product_id) return line ? { ...line } : line;
 const product = (data.products || []).find((pr) => pr.id === line.product_id);
 if (!product) return { ...line };
 const pricingArray = (product.pricing && product.pricing.length > 0)
 ? product.pricing
 : [{
 unit: 'each',
 retail_price: product.retail_price || product.selling_price || product.wholesale_price || 0,
 wholesale_price: product.wholesale_price || product.retail_price || product.selling_price || 0,
 }];
 const pricing = pricingArray.find((pr) => pr.unit === line.unit) || pricingArray[0];
 if (!pricing) return { ...line };
 const updatedPrice = orderType === 'retail' ? pricing.retail_price : pricing.wholesale_price;
 return { ...line, unit_price: updatedPrice ?? line.unit_price };
 });
 return { ...prev, salesOrder: { ...prev.salesOrder, order_type: orderType, lines } };
 });
 return;
 }
 setForms((prev) => ({ ...prev, [formName]: { ...prev[formName], [field]: value } }));
 }

 // Sales Order line helpers
 function addSalesLine() {
 setForms((p) => ({ ...p, salesOrder: { ...p.salesOrder, lines: [...p.salesOrder.lines, { product_id: '', unit: '', quantity: '', unit_price: '' }] } }));
 }
 
 async function updateSalesLine(index, field, value) {
 setForms((p) => {
 const lines = [...p.salesOrder.lines];
 lines[index] = { ...lines[index], [field]: value };
 
 // Auto-calculate price and default unit when product changes or unit changes
 const orderType = p.salesOrder.order_type || 'retail';
 if (field === 'product_id') {
 const productId = value;
 const product = (data.products || []).find(pr => pr.id === productId);
 // Build a pricing array (fallback to synthetic single-unit pricing if none provided)
 const pricingArray = (product && product.pricing && product.pricing.length > 0)
 ? product.pricing
 : (product ? [{
 unit: 'each',
 retail_price: product.retail_price || product.selling_price || product.wholesale_price || 0,
 wholesale_price: product.wholesale_price || product.retail_price || product.selling_price || 0
 }] : []);
 if (pricingArray.length === 1) {
 const only = pricingArray[0];
 lines[index].unit = only.unit || 'each';
 lines[index].unit_price = orderType === 'retail' ? only.retail_price : only.wholesale_price;
 } else {
 lines[index].unit = '';
 lines[index].unit_price = '';
 }
 }

 if (field === 'unit') {
 const productId = lines[index].product_id;
 const unit = value;
 if (productId && unit) {
 const product = (data.products || []).find(pr => pr.id === productId);
 const pricingArray = (product && product.pricing && product.pricing.length > 0)
 ? product.pricing
 : (product ? [{
 unit: 'each',
 retail_price: product.retail_price || product.selling_price || product.wholesale_price || 0,
 wholesale_price: product.wholesale_price || product.retail_price || product.selling_price || 0
 }] : []);
 const pricing = pricingArray.find(pr => pr.unit === unit);
 if (pricing) {
 lines[index].unit_price = orderType === 'retail' ? pricing.retail_price : pricing.wholesale_price;
 }
 }
 }
 
 return { ...p, salesOrder: { ...p.salesOrder, lines } };
 });
 }
 function removeSalesLine(index) {
 setForms((p) => {
 const lines = [...p.salesOrder.lines];
 lines.splice(index, 1);
 return { ...p, salesOrder: { ...p.salesOrder, lines } };
 });
 }

 // Product pricing functions
 function addProductPricing() {
 setForms((p) => ({ ...p, product: { ...p.product, pricing: [...(p.product.pricing || []), { unit: '', cost_price: '', retail_price: '', wholesale_price: '' }] } }));
 }
 function updateProductPricing(index, field, value) {
 setForms((p) => {
 const pricing = [...(p.product.pricing || [])];
 pricing[index] = { ...pricing[index], [field]: value };
 return { ...p, product: { ...p.product, pricing } };
 });
 }
 function removeProductPricing(index) {
 setForms((p) => {
 const pricing = [...(p.product.pricing || [])];
 pricing.splice(index, 1);
 return { ...p, product: { ...p.product, pricing } };
 });
 }

 async function saveItem(entity, endpoint) {
 try {
 setLoading(true);
 let payload = {};
 if (entity === 'staff') payload = { ...forms.staff };
 if (entity === 'product') payload = { ...forms.product };
 if (entity === 'rawMaterial') payload = { ...forms.rawMaterial };
 if (entity === 'warehouse') payload = { ...forms.warehouse };
 if (entity === 'customer') payload = { ...forms.customer };

 // Clean payload: convert empty strings to null for optional fields, remove empty numeric fields
 Object.keys(payload).forEach(key => {
 if (payload[key] === '' && !['first_name', 'last_name', 'name', 'sku', 'code'].includes(key)) {
 payload[key] = null;
 }
 // Remove empty numeric fields like hourly_rate, monthly_salary
 if (['hourly_rate', 'monthly_salary', 'cost_price', 'selling_price', 'retail_price', 'wholesale_price', 'unit_cost', 'reorder_level'].includes(key) && (payload[key] === '' || payload[key] === null)) {
 delete payload[key];
 }
 });

 const method = editingItem?.id ? 'PUT' : 'POST';
 const url = editingItem?.id ? `${endpoint.replace(/\/+$/, '')}/${editingItem.id}` : endpoint;

 const token = localStorage.getItem('access_token');
 const headers = { 'Content-Type': 'application/json' };
 if (token) headers.Authorization = `Bearer ${token}`;

 const res = await authedFetch(url, { method, headers, body: JSON.stringify(payload) });
 if (!res.ok) throw new Error((await res.json()).detail || 'Save failed');
 notify(`${entity} saved`, 'success');
 setShowForm('');
 setEditingItem(null);
 // Refresh relevant module
 const moduleKey = entity === 'rawMaterial' ? 'rawMaterials' : (entity === 'product' ? 'products' : (entity === 'staff' ? 'staff' : (entity === 'customer' ? 'customers' : 'warehouses')));
 fetchData(moduleKey);
 } catch (e) {
 notify(`Error saving ${entity}: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // Delete item function
 async function deleteItem(entity, itemId) {
 if (!confirm(`Are you sure you want to delete this ${entity}?`)) return;
 try {
 setLoading(true);
 let endpoint = '';
 if (entity === 'product') endpoint = '/api/products/';
 if (entity === 'rawMaterial') endpoint = '/api/raw-materials/';
 if (entity === 'staff') endpoint = '/api/staff/staffs/';
 if (entity === 'warehouse') endpoint = '/api/warehouses/';
 if (entity === 'customer') endpoint = '/api/sales/customers/';
 
 const token = localStorage.getItem('access_token');
 const headers = token ? { Authorization: `Bearer ${token}` } : {};
 const res = await authedFetch(`${endpoint}${itemId}`, { method: 'DELETE', headers });
 if (!res.ok) throw new Error(`Failed to delete ${entity}`);
 notify(`${entity} deleted`, 'success');
 
 // Refresh relevant module
 const moduleKey = entity === 'rawMaterial' ? 'rawMaterials' : (entity === 'product' ? 'products' : (entity === 'staff' ? 'staff' : (entity === 'customer' ? 'customers' : 'warehouses')));
 fetchData(moduleKey);
 } catch (e) {
 notify(`Error deleting ${entity}: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // Production console quick action
 async function processProduction(productId, qty) {
 if (!productId || !qty) return;
 try {
 setLoading(true);
 const url = `/api/production/execute-production?product_id=${encodeURIComponent(productId)}&quantity=${encodeURIComponent(qty)}`;
 const res = await authedFetch(url, { method: 'POST' });
 if (!res.ok) throw new Error((await res.json()).detail || 'Production failed');
 const j = await res.json();
 notify(j.message || 'Production executed', 'success');
 fetchData('production');
 fetchData('stock');
 } catch (e) {
 notify(`Production error: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 async function generateInvoice(orderId) {
 try {
 setLoading(true);
 const order = (data.sales||[]).find(o => o.id === orderId);
 const customer = order ? (data.customers||[]).find(c => c.id === order.customer_id) : null;
 const custName = (customer ? customer.name : 'Customer').replace(/\s+/g, '_');
 const orderDate = order && order.order_date ? new Date(order.order_date).toISOString().slice(0,10) : new Date().toISOString().slice(0,10);
 const res = await authedFetch(`/api/sales/generate-invoice-pdf/${orderId}`);
 if (!res.ok) throw new Error('Failed to generate invoice');
 const blob = await res.blob();
 const url = window.URL.createObjectURL(blob);
 const a = document.createElement('a');
 a.href = url; a.download = `Invoice-${custName}-${orderDate}.pdf`; document.body.appendChild(a); a.click(); a.remove();
 window.URL.revokeObjectURL(url);
 notify('Invoice downloaded', 'success');
 } catch (e) {
 notify(`Invoice error: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // Delete Sales Order (Admin Only)
 async function deleteSalesOrder(orderId) {
 const order = (data.sales||[]).find(o => o.id === orderId);
 const customer = order ? (data.customers||[]).find(c => c.id === order.customer_id) : null;
 const orderLabel = order ? `${order.order_number} (${customer ? customer.name : 'Unknown'})` : orderId;
 if (!window.confirm(`Are you sure you want to DELETE sales order ${orderLabel}? This action cannot be undone. Stock will be restored if applicable.`)) return;
 try {
 setLoading(true);
 const token = localStorage.getItem('access_token');
 const headers = {};
 if (token) headers.Authorization = `Bearer ${token}`;
 const res = await authedFetch(`/api/sales/orders/${orderId}`, { method: 'DELETE', headers });
 if (!res.ok) {
 const err = await res.json();
 throw new Error(err.detail || 'Failed to delete order');
 }
 notify(`Sales order ${orderLabel} deleted successfully`, 'success');
 fetchData('sales');
 fetchData('stock');
 } catch (e) {
 notify(`Delete error: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 async function processOrder(orderId) {
 try {
 setLoading(true);
 const res = await authedFetch(`/api/sales/process-order/${orderId}`, { method: 'POST' });
 if (!res.ok) {
 const errorData = await res.json();
 const errorMsg = errorData.detail || 'Failed to process order';
 
 // Check if it's an insufficient stock error
 if (errorMsg.toLowerCase().includes('insufficient stock')) {
 notify(' INSUFFICIENT STOCK: Cannot process order. Please go to Stock Management  Product Stock Intake to add stock first, then try again.', 'error');
 } else {
 throw new Error(errorMsg);
 }
 return;
 }
 const j = await res.json();
 notify(j.message || 'Order processed', 'success');
 fetchData('sales');
 fetchData('stock');
 } catch (e) {
 notify(`Process order error: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // Mark Order as Paid
 async function markOrderAsPaid(orderId) {
 try {
 setLoading(true);
 const res = await authedFetch(`/api/sales/orders/${orderId}/mark-paid`, { method: 'PATCH' });
 if (!res.ok) throw new Error((await res.json()).detail || 'Failed to mark as paid');
 notify('Order marked as paid, receipt generated', 'success');
 // Auto-download receipt using the proper download function
 await downloadReceipt(orderId);
 fetchData('sales');
 } catch (e) {
 notify(`Error: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // Download Invoice
 async function downloadInvoice(orderId) {
 try {
 const order = (data.sales||[]).find(o => o.id === orderId);
 const customer = order ? (data.customers||[]).find(c => c.id === order.customer_id) : null;
 const custName = (customer ? customer.name : 'Customer').replace(/\s+/g, '_');
 const orderDate = order && order.order_date ? new Date(order.order_date).toISOString().slice(0,10) : new Date().toISOString().slice(0,10);
 const url = `/api/sales/orders/${orderId}/generate-invoice`;
 console.log('Fetching invoice from:', url);
 const response = await authedFetch(url, { method: 'POST' });
 console.log('Invoice response status:', response.status);
 if (!response.ok) {
 const error = await response.json();
 console.error('Invoice error response:', error);
 notify(`Invoice error: ${error.detail || 'Failed to generate invoice'}`, 'error');
 return;
 }
 const blob = await response.blob();
 const objectUrl = window.URL.createObjectURL(blob);
 const a = document.createElement('a');
 a.href = objectUrl;
 a.download = `Invoice-${custName}-${orderDate}.pdf`;
 document.body.appendChild(a);
 a.click();
 a.remove();
 window.URL.revokeObjectURL(objectUrl);
 notify('Invoice downloaded successfully', 'success');
 } catch (e) {
 console.error('Invoice download exception:', e);
 notify(`Invoice error: ${e.message}`, 'error');
 }
 }

 // Download Receipt
 async function downloadReceipt(orderId) {
 try {
 const order = (data.sales||[]).find(o => o.id === orderId);
 const customer = order ? (data.customers||[]).find(c => c.id === order.customer_id) : null;
 const custName = (customer ? customer.name : 'Customer').replace(/\s+/g, '_');
 const orderDate = order && order.order_date ? new Date(order.order_date).toISOString().slice(0,10) : new Date().toISOString().slice(0,10);
 const url = `/api/sales/orders/${orderId}/generate-receipt`;
 console.log('Fetching receipt from:', url);
 const response = await authedFetch(url, { method: 'POST' });
 console.log('Receipt response status:', response.status);
 if (!response.ok) {
 const error = await response.json();
 console.error('Receipt error response:', error);
 notify(`Receipt error: ${error.detail || 'Failed to generate receipt'}`, 'error');
 return;
 }
 const blob = await response.blob();
 const objectUrl = window.URL.createObjectURL(blob);
 const a = document.createElement('a');
 a.href = objectUrl;
 a.download = `Receipt-${custName}-${orderDate}.pdf`;
 document.body.appendChild(a);
 a.click();
 a.remove();
 window.URL.revokeObjectURL(objectUrl);
 notify('Receipt downloaded successfully', 'success');
 } catch (e) {
 console.error('Receipt download exception:', e);
 notify(`Receipt error: ${e.message}`, 'error');
 }
 }

 // Sales Order submit
 // Fetch unpaid orders for a customer
 async function fetchCustomerUnpaidOrders(customerId) {
 if (!customerId) { setCustomerUnpaidOrders(null); return; }
 try {
 const res = await authedFetch(`/api/sales/customer/${customerId}/unpaid`);
 if (res.ok) {
 const result = await res.json();
 setCustomerUnpaidOrders(result.count > 0 ? result : null);
 } else {
 setCustomerUnpaidOrders(null);
 }
 } catch (e) {
 console.error('Error fetching customer unpaid orders:', e);
 setCustomerUnpaidOrders(null);
 }
 }

 async function submitSalesOrder(e) {
 e.preventDefault();
 try {
 if (!forms.salesOrder.customer_id) throw new Error('Customer required');
 if (!accessibleWarehouses.length) throw new Error('Warehouse access required before creating orders');
 if (!forms.salesOrder.warehouse_id) throw new Error('Warehouse required');
 if (!forms.salesOrder.lines.length) throw new Error('Add at least one line');
 setLoading(true);
 const payload = {
 customer_id: forms.salesOrder.customer_id,
 warehouse_id: forms.salesOrder.warehouse_id,
 required_date: forms.salesOrder.required_date ? `${forms.salesOrder.required_date}T00:00:00` : null,
 notes: forms.salesOrder.notes || null,
 payment_status: forms.salesOrder.payment_status || 'unpaid',
 order_type: forms.salesOrder.order_type || 'retail',
 lines: forms.salesOrder.lines.map(l => ({
 product_id: l.product_id,
 quantity: Number(l.quantity || 0),
 unit_price: Number(l.unit_price || 0),
 }))
 };
 const token = localStorage.getItem('access_token');
 const headers = { 'Content-Type': 'application/json' };
 if (token) {
 headers.Authorization = `Bearer ${token}`;
 }
 const res = await authedFetch('/api/sales/orders', { method: 'POST', headers, body: JSON.stringify(payload) });
 if (!res.ok) throw new Error((await res.json()).detail || 'Failed to create order');
 
 const result = await res.json();
 // Auto-download invoice or receipt based on payment status
 if (forms.salesOrder.payment_status === 'paid' && result.id) {
 await downloadReceipt(result.id);
 notify('Sales order created and receipt downloaded', 'success');
 } else if (result.id) {
 await downloadInvoice(result.id);
 notify('Sales order created and invoice downloaded', 'success');
 } else {
 notify('Sales order created', 'success');
 }
 
 setShowForm('');
 resetForm('salesOrder');
 setCustomerUnpaidOrders(null);
 fetchData('sales');
 } catch (e) {
 notify(`Sales order error: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // Production Order submit
 async function submitProductionOrder(e) {
 e.preventDefault();
 try {
 if (!forms.productionOrder.product_id || !forms.productionOrder.quantity_planned) throw new Error('Product and quantity required');
 setLoading(true);
 const payload = {
 product_id: forms.productionOrder.product_id,
 quantity_planned: Number(forms.productionOrder.quantity_planned),
 scheduled_start_date: forms.productionOrder.scheduled_start_date ? `${forms.productionOrder.scheduled_start_date}T00:00:00` : null,
 scheduled_end_date: forms.productionOrder.scheduled_end_date ? `${forms.productionOrder.scheduled_end_date}T00:00:00` : null,
 priority: Number(forms.productionOrder.priority || 5),
 notes: forms.productionOrder.notes || null,
 };
 console.log('Production order payload:', payload);
 const res = await authedFetch('/api/production/orders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
 if (!res.ok) {
 const errorData = await res.json();
 console.error('Production order error:', errorData);
 throw new Error(errorData.detail || 'Failed to create production order');
 }
 notify('Production order created', 'success');
 setShowForm('');
 resetForm('productionOrder');
 fetchData('production');
 } catch (e) {
 notify(`Production order error: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // Payroll submit (calculate + auto-download payslip by payroll_id)
 async function submitPayroll(e) {
 e.preventDefault();
 try {
 if (!forms.payroll.staff_id || !forms.payroll.pay_period_start || !forms.payroll.pay_period_end) throw new Error('Complete the fields');
 setLoading(true);
 const payload = { ...forms.payroll };
 const res = await authedFetch('/api/staff/payroll/calculate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
 if (!res.ok) throw new Error((await res.json()).detail || 'Failed to calculate payroll');
 const payroll = await res.json();
 notify('Payroll calculated', 'success');
 // Auto download payslip by payroll_id
 const pdf = await authedFetch(`/api/staff/payslip/${payroll.id}/pdf`);
 if (pdf.ok) {
 const blob = await pdf.blob();
 const url = window.URL.createObjectURL(blob);
 const a = document.createElement('a'); a.href = url; a.download = `payslip_${payroll.id}.pdf`; document.body.appendChild(a); a.click(); a.remove();
 window.URL.revokeObjectURL(url);
 notify('Payslip downloaded', 'success');
 }
 setShowForm('');
 resetForm('payroll');
 } catch (e) {
 notify(`Payroll error: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // ==================== BOM & PRODUCTION REQUIREMENT FUNCTIONS ====================
 
 // Calculate production requirements
 async function calculateProductionRequirements() {
 try {
 if (!forms.production.product_id || !forms.production.quantity_to_produce) {
 notify('Please select product and quantity', 'error');
 return;
 }
 
 if (!forms.production.warehouse_id) {
 notify('Please select warehouse', 'error');
 return;
 }
 
 setLoading(true);
 const url = `/api/bom/calculate-requirements?product_id=${forms.production.product_id}&quantity=${forms.production.quantity_to_produce}&warehouse_id=${forms.production.warehouse_id}`;
 const res = await authedFetch(url);
 
 if (!res.ok) {
 const error = await res.json();
 throw new Error(error.detail || 'Failed to calculate requirements');
 }
 
 const requirements = await res.json();
 setProductionRequirements(requirements);
 
 if (requirements.can_produce) {
 notify(`Requirements calculated: ₦${requirements.total_material_cost.toFixed(2)} material cost`, 'success');
 } else {
 notify(' Insufficient stock! Check shortages below', 'warning');
 }
 } catch (e) {
 notify(`Error: ${e.message}`, 'error');
 setProductionRequirements(null);
 } finally {
 setLoading(false);
 }
 }
 
 // Approve production and deduct stock
 async function approveProductionAndDeduct() {
 try {
 if (!productionRequirements) {
 notify('Calculate requirements first', 'error');
 return;
 }
 
 if (!productionRequirements.can_produce) {
 notify('Cannot approve production due to stock shortages', 'error');
 return;
 }
 
 setLoading(true);
 const payload = {
 product_id: productionRequirements.product_id,
 quantity: productionRequirements.quantity_to_produce,
 warehouse_id: productionRequirements.warehouse_id,
 notes: `Production approved via console on ${new Date().toLocaleString()}`
 };
 
 const res = await authedFetch('/api/bom/approve-production', {
 method: 'POST',
 headers: { 'Content-Type': 'application/json' },
 body: JSON.stringify(payload)
 });
 
 if (!res.ok) {
 const error = await res.json();
 throw new Error(error.detail?.error || error.detail || 'Failed to approve production');
 }
 
 const result = await res.json();
 notify(` ${result.message}`, 'success');
 
 // Reset production form and requirements
 resetForm('production');
 setProductionRequirements(null);
 
 // Refresh data
 fetchAllData();
 } catch (e) {
 notify(`Production approval error: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }
 
 // Fetch all registered BOMs
 async function fetchAllBoms() {
 try {
 const res = await authedFetch('/api/bom/products-with-bom');
 if (res.ok) {
 const boms = await res.json();
 setAllBoms(Array.isArray(boms) ? boms : []);
 }
 } catch (e) {
 console.error('Error fetching BOMs:', e);
 }
 }

 // Create/Update BOM
 async function saveBOM() {
 try {
 if (!forms.bom.product_id || bomLines.length === 0) {
 notify('Please select product and add at least one raw material', 'error');
 return;
 }
 
 setLoading(true);
 const payload = {
 product_id: forms.bom.product_id,
 lines: bomLines.map(line => ({
 raw_material_id: line.raw_material_id,
 qty_per_unit: parseFloat(line.qty_per_unit),
 unit: line.unit
 }))
 };
 
 console.log('BOM payload:', payload);
 const res = await authedFetch('/api/bom/create', {
 method: 'POST',
 headers: { 'Content-Type': 'application/json' },
 body: JSON.stringify(payload)
 });
 
 if (!res.ok) {
 const error = await res.json();
 console.error('BOM error:', error);
 throw new Error(error.detail || 'Failed to save BOM');
 }
 
 const result = await res.json();
 notify(result.message, 'success');
 
 // Reset form
 setShowForm('');
 resetForm('bom');
 setBomLines([]);
 setEditingBomProductId(null);
 fetchAllData();
 fetchAllBoms();
 } catch (e) {
 notify(`BOM save error: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // Edit existing BOM - load into form
 async function editBOM(productId) {
 try {
 setLoading(true);
 const res = await authedFetch(`/api/bom/product/${productId}`);
 if (!res.ok) throw new Error('Failed to fetch BOM');
 const bom = await res.json();
 if (!bom.has_bom) {
 notify('No BOM found for this product', 'error');
 return;
 }
 // Populate the form
 handleFormChange('bom', 'product_id', productId);
 setEditingBomProductId(productId);
 setBomLines(bom.lines.map(line => ({
 id: line.id || Date.now() + Math.random(),
 raw_material_id: line.raw_material_id,
 qty_per_unit: line.qty_per_unit,
 unit: line.unit || 'kg'
 })));
 setShowForm('bom');
 } catch (e) {
 notify(`Error loading BOM: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // Delete BOM
 async function deleteBOM(productId, productName) {
 if (!window.confirm(`Are you sure you want to delete the BOM for "${productName}"? This cannot be undone.`)) return;
 try {
 setLoading(true);
 const res = await authedFetch(`/api/bom/product/${productId}`, { method: 'DELETE' });
 if (!res.ok) throw new Error('Failed to delete BOM');
 notify('BOM deleted successfully', 'success');
 fetchAllBoms();
 } catch (e) {
 notify(`Error deleting BOM: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }
 
 // Add BOM line
 function addBomLine() {
 setBomLines([...bomLines, { id: Date.now(), raw_material_id: '', qty_per_unit: '', unit: 'kg' }]);
 }
 
 // Remove BOM line
 function removeBomLine(lineId) {
 setBomLines(bomLines.filter(line => line.id !== lineId));
 }
 
 // Update BOM line
 function updateBomLine(lineId, field, value) {
 setBomLines(bomLines.map(line => 
 line.id === lineId ? { ...line, [field]: value } : line
 ));
 }
 
 // View product BOM
 async function viewProductBOM(productId) {
 try {
 setLoading(true);
 const res = await authedFetch(`/api/bom/product/${productId}`);
 
 if (!res.ok) {
 throw new Error('Failed to fetch BOM');
 }
 
 const bom = await res.json();
 
 if (!bom.has_bom) {
 notify('No BOM defined for this product', 'info');
 return;
 }
 
 // Display BOM details
 let html = `
 <div class="bom-details">
 <h3>Bill of Materials: ${bom.product_name}</h3>
 <p><strong>Product SKU:</strong> ${bom.product_sku}</p>
 <p><strong>BOM Version:</strong> ${bom.version}</p>
 <p><strong>Raw Materials:</strong> ${bom.lines_count}</p>
 <p><strong>Total Material Cost per Unit:</strong> ${formatCurrency(bom.total_material_cost)}</p>
 
 <table class="stock-table">
 <thead>
 <tr>
 <th>Raw Material</th>
 <th>SKU</th>
 <th>Qty per Unit</th>
 <th>Unit</th>
 <th>Unit Cost</th>
 <th>Line Cost</th>
 </tr>
 </thead>
 <tbody>
 ${bom.lines.map(line => `
 <tr>
 <td>${line.raw_material_name}</td>
 <td>${line.raw_material_sku}</td>
 <td>${line.qty_per_unit}</td>
 <td>${line.unit}</td>
 <td>${formatCurrency(line.unit_cost)}</td>
 <td><strong>${formatCurrency(line.line_cost)}</strong></td>
 </tr>
 `).join('')}
 </tbody>
 <tfoot>
 <tr>
 <td colspan="5" style="text-align: right;"><strong>Total Material Cost:</strong></td>
 <td><strong>${formatCurrency(bom.total_material_cost)}</strong></td>
 </tr>
 </tfoot>
 </table>
 </div>
 `;
 
 setStockDisplayArea(html);
 } catch (e) {
 notify(`Error fetching BOM: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // ==================== STOCK MANAGEMENT FUNCTIONS ====================
 
 // Fetch stock levels
 async function fetchStockLevels(type = 'all') {
 try {
 setLoading(true);
 if (type === 'products' || type === 'all') {
 const res = await authedFetch('/api/stock-management/product-levels');
 if (res.ok) {
 const productLevels = await res.json();
 setStockLevels(prev => ({ ...prev, products: productLevels }));
 }
 }
 if (type === 'rawMaterials' || type === 'all') {
 const res = await authedFetch('/api/stock-management/raw-material-levels');
 if (res.ok) {
 const rawMaterialLevels = await res.json();
 setStockLevels(prev => ({ ...prev, rawMaterials: rawMaterialLevels }));
 }
 }
 } catch (e) {
 notify(`Error fetching stock levels: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // Fetch stock analysis
 async function fetchStockAnalysis() {
 try {
 setLoading(true);
 const res = await authedFetch('/api/stock-management/analysis');
 if (res.ok) {
 const analysis = await res.json();
 setStockAnalysis(analysis);
 }
 } catch (e) {
 notify(`Error fetching stock analysis: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // Submit product stock intake
 async function submitProductStockIntake(e) {
 e.preventDefault();
 try {
 if (!forms.stockIntake.warehouse_id || !forms.stockIntake.product_id || !forms.stockIntake.quantity || !forms.stockIntake.unit_cost) {
 throw new Error('Please fill all required fields');
 }
 if (!forms.stockIntake.unit) {
 throw new Error('Please select a unit');
 }
 setLoading(true);
 const payload = {
 warehouse_id: forms.stockIntake.warehouse_id,
 product_id: forms.stockIntake.product_id,
 quantity: parseFloat(forms.stockIntake.quantity),
 unit_cost: parseFloat(forms.stockIntake.unit_cost),
 unit: forms.stockIntake.unit,
 supplier: forms.stockIntake.supplier || null,
 batch_number: forms.stockIntake.batch_number || null,
 notes: forms.stockIntake.notes || null,
 };
 const res = await authedFetch('/api/stock-management/product-intake', {
 method: 'POST',
 headers: { 'Content-Type': 'application/json' },
 body: JSON.stringify(payload)
 });
 if (!res.ok) throw new Error((await res.json()).detail || 'Failed to record product intake');
 const result = await res.json();
 notify(result.message, 'success');
 setShowForm('');
 resetForm('stockIntake');
 fetchStockLevels('products');
 } catch (e) {
 notify(`Product intake error: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // Submit raw material stock intake
 async function submitRawMaterialStockIntake(e) {
 e.preventDefault();
 try {
 if (!forms.rawMaterialIntake.warehouse_id || !forms.rawMaterialIntake.raw_material_id || !forms.rawMaterialIntake.quantity || !forms.rawMaterialIntake.unit_cost) {
 throw new Error('Please fill all required fields');
 }
 setLoading(true);
 const payload = {
 warehouse_id: forms.rawMaterialIntake.warehouse_id,
 raw_material_id: forms.rawMaterialIntake.raw_material_id,
 quantity: parseFloat(forms.rawMaterialIntake.quantity),
 unit_cost: parseFloat(forms.rawMaterialIntake.unit_cost),
 supplier: forms.rawMaterialIntake.supplier || null,
 batch_number: forms.rawMaterialIntake.batch_number || null,
 notes: forms.rawMaterialIntake.notes || null,
 };
 const res = await authedFetch('/api/stock-management/raw-material-intake', {
 method: 'POST',
 headers: { 'Content-Type': 'application/json' },
 body: JSON.stringify(payload)
 });
 if (!res.ok) throw new Error((await res.json()).detail || 'Failed to record raw material intake');
 const result = await res.json();
 notify(result.message, 'success');
 setShowForm('');
 resetForm('rawMaterialIntake');
 fetchStockLevels('rawMaterials');
 } catch (e) {
 notify(`Raw material intake error: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // Submit damaged product
 async function submitDamagedProduct(e) {
 e.preventDefault();
 try {
 if (!forms.damagedProduct.warehouse_id || !forms.damagedProduct.product_id || !forms.damagedProduct.quantity || !forms.damagedProduct.damage_type) {
 throw new Error('Please fill all required fields');
 }
 setLoading(true);
 const params = new URLSearchParams({
 warehouse_id: forms.damagedProduct.warehouse_id,
 product_id: forms.damagedProduct.product_id,
 quantity: parseFloat(forms.damagedProduct.quantity),
 damage_type: forms.damagedProduct.damage_type,
 });
 if (forms.damagedProduct.damage_reason) params.append('damage_reason', forms.damagedProduct.damage_reason);
 if (forms.damagedProduct.notes) params.append('notes', forms.damagedProduct.notes);
 
 console.log('Damaged product params:', params.toString());
 const res = await authedFetch(`/api/stock-management/damaged-product?${params.toString()}`, {
 method: 'POST'
 });
 if (!res.ok) {
 const errorData = await res.json();
 console.error('Damaged product error:', errorData);
 throw new Error(errorData.detail || 'Failed to record damaged product');
 }
 const result = await res.json();
 notify(result.message, 'success');
 setShowForm('');
 resetForm('damagedProduct');
 fetchStockLevels('products');
 } catch (e) {
 notify(`Damaged product error: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // Submit damaged raw material
 async function submitDamagedRawMaterial(e) {
 e.preventDefault();
 try {
 if (!forms.damagedRawMaterial.warehouse_id || !forms.damagedRawMaterial.raw_material_id || !forms.damagedRawMaterial.quantity || !forms.damagedRawMaterial.damage_type) {
 throw new Error('Please fill all required fields');
 }
 setLoading(true);
 const params = new URLSearchParams({
 warehouse_id: forms.damagedRawMaterial.warehouse_id,
 raw_material_id: forms.damagedRawMaterial.raw_material_id,
 quantity: parseFloat(forms.damagedRawMaterial.quantity),
 damage_type: forms.damagedRawMaterial.damage_type,
 });
 if (forms.damagedRawMaterial.damage_reason) params.append('damage_reason', forms.damagedRawMaterial.damage_reason);
 if (forms.damagedRawMaterial.notes) params.append('notes', forms.damagedRawMaterial.notes);
 
 console.log('Damaged raw material params:', params.toString());
 const res = await authedFetch(`/api/stock-management/damaged-raw-material?${params.toString()}`, {
 method: 'POST'
 });
 if (!res.ok) {
 const errorData = await res.json();
 console.error('Damaged raw material error:', errorData);
 throw new Error(errorData.detail || 'Failed to record damaged raw material');
 }
 const result = await res.json();
 notify(result.message, 'success');
 setShowForm('');
 resetForm('damagedRawMaterial');
 fetchStockLevels('rawMaterials');
 } catch (e) {
 notify(`Damaged raw material error: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // Submit returned product
 async function submitReturnedProduct(e) {
 e.preventDefault();
 try {
 if (!forms.returnedProduct.warehouse_id || !forms.returnedProduct.product_id || !forms.returnedProduct.quantity || !forms.returnedProduct.return_reason || !forms.returnedProduct.return_condition) {
 throw new Error('Please fill all required fields');
 }
 setLoading(true);
 const params = new URLSearchParams({
 warehouse_id: forms.returnedProduct.warehouse_id,
 product_id: forms.returnedProduct.product_id,
 quantity: parseFloat(forms.returnedProduct.quantity),
 return_reason: forms.returnedProduct.return_reason,
 return_condition: forms.returnedProduct.return_condition,
 refund_amount: parseFloat(forms.returnedProduct.refund_amount) || 0,
 });
 if (forms.returnedProduct.customer_name) params.append('customer_name', forms.returnedProduct.customer_name);
 if (forms.returnedProduct.notes) params.append('notes', forms.returnedProduct.notes);
 
 console.log('Returned product params:', params.toString());
 const res = await authedFetch(`/api/stock-management/returned-product?${params.toString()}`, {
 method: 'POST'
 });
 if (!res.ok) {
 const errorData = await res.json();
 console.error('Returned product error:', errorData);
 throw new Error(errorData.detail || 'Failed to record product return');
 }
 const result = await res.json();
 notify(result.message, 'success');
 setShowForm('');
 resetForm('returnedProduct');
 fetchStockLevels('products');
 } catch (e) {
 notify(`Product return error: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // Admin stock level adjustment
 async function adminAdjustStockLevel(stockLevelId, itemName, currentStock, type = 'product', units = '') {
 const isAdmin = !currentUser || currentUser.role === 'admin';
 if (!isAdmin) { notify('Only administrators can adjust stock levels', 'error'); return; }
 const unitDisplay = units ? ` (${units})` : '';
 const newVal = prompt(`Adjust stock for: ${itemName}\nAvailable Units: ${units || 'N/A'}\nCurrent Stock: ${currentStock}${unitDisplay}\n\nEnter new stock level${unitDisplay}:`, currentStock);
 if (newVal === null) return;
 const newStock = parseFloat(newVal);
 if (isNaN(newStock) || newStock < 0) { notify('Please enter a valid non-negative number', 'error'); return; }
 const reason = prompt('Reason for adjustment (optional):');
 try {
 setLoading(true);
 const res = await authedFetch('/api/stock-management/adjust-stock-level', {
 method: 'PUT',
 headers: { 'Content-Type': 'application/json' },
 body: JSON.stringify({ stock_level_id: stockLevelId, new_stock: newStock, reason: reason || '' })
 });
 if (!res.ok) { const d = await res.json(); throw new Error(d.detail || 'Failed to adjust stock'); }
 const result = await res.json();
 notify(result.message, 'success');
 // Refresh the view
 if (type === 'product') viewProductStockLevels(false);
 else viewRawMaterialStockLevels(false);
 } catch (e) {
 notify(`Stock adjustment error: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // Expose to window for dangerouslySetInnerHTML onclick
 window.adminAdjustStockLevel = adminAdjustStockLevel;

 // View product stock levels
 async function viewProductStockLevels(lowStockOnly = false) {
 try {
 setLoading(true);
 const url = lowStockOnly ? '/api/stock-management/product-levels?low_stock_only=true' : '/api/stock-management/product-levels';
 const res = await authedFetch(url);
 if (!res.ok) throw new Error('Failed to fetch product stock levels');
 const levels = await res.json();
 const isAdmin = !currentUser || currentUser.role === 'admin';
 
 let html = `
 <div class="stock-levels-section">
 <h3>${lowStockOnly ? 'WARNING: Low Stock Products' : 'Product Stock Levels'}</h3>
 <table class="stock-table">
 <thead>
 <tr>
 <th>Product</th>
 <th>SKU</th>
 <th>Warehouse</th>
 <th>Units</th>
 <th>Current Stock</th>
 <th>Reserved</th>
 <th>Available</th>
 <th>Reorder Level</th>
 <th>Status</th>
 ${isAdmin ? '<th>Action</th>' : ''}
 </tr>
 </thead>
 <tbody>
 ${levels.map(l => {
 const stockUnit = l.product_unit || '';
 const unitLabel = stockUnit ? ' ' + stockUnit : '';
 return `
 <tr class="${l.is_low_stock ? 'low-stock-row' : ''}">
 <td>${l.product_name}</td>
 <td>${l.product_sku}</td>
 <td>${l.warehouse_name}</td>
 <td><span style="background:#e8eaf6;color:#3949ab;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;">${stockUnit || 'N/A'}</span></td>
 <td>${l.current_stock}${unitLabel}</td>
 <td>${l.reserved_stock}${unitLabel}</td>
 <td><strong>${l.available_stock}${unitLabel}</strong></td>
 <td>${l.reorder_level}${unitLabel}</td>
 <td>${l.is_low_stock ? '<span class="status-badge low-stock">LOW STOCK</span>' : '<span class="status-badge ok-stock">OK</span>'}</td>
 ${isAdmin ? `<td><button onclick="window.adminAdjustStockLevel('${l.stock_level_id}','${(l.product_name||'').replace(/'/g, "\\'").replace(/"/g, '&quot;')}',${l.current_stock},'product','${(stockUnit||'').replace(/'/g, "\\'")}')"
 style="background:#667eea;color:#fff;border:none;border-radius:4px;padding:4px 12px;cursor:pointer;font-size:12px;font-weight:600;">Edit</button></td>` : ''}
 </tr>
 `}).join('')}
 </tbody>
 </table>
 </div>
 `;
 setStockDisplayArea(html);
 } catch (e) {
 notify(`Error: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // View raw material stock levels
 async function viewRawMaterialStockLevels(lowStockOnly = false) {
 try {
 setLoading(true);
 const url = lowStockOnly ? '/api/stock-management/raw-material-levels?low_stock_only=true' : '/api/stock-management/raw-material-levels';
 const res = await authedFetch(url);
 if (!res.ok) throw new Error('Failed to fetch raw material stock levels');
 const levels = await res.json();
 const isAdmin = !currentUser || currentUser.role === 'admin';
 
 let html = `
 <div class="stock-levels-section">
 <h3>${lowStockOnly ? 'WARNING: Low Stock Raw Materials' : 'Raw Material Stock Levels'}</h3>
 <table class="stock-table">
 <thead>
 <tr>
 <th>Raw Material</th>
 <th>SKU</th>
 <th>Warehouse</th>
 <th>Current Stock</th>
 <th>Reserved</th>
 <th>Available</th>
 <th>Unit</th>
 <th>Reorder Level</th>
 <th>Status</th>
 ${isAdmin ? '<th>Action</th>' : ''}
 </tr>
 </thead>
 <tbody>
 ${levels.map(l => `
 <tr class="${l.is_low_stock ? 'low-stock-row' : ''}">
 <td>${l.raw_material_name}</td>
 <td>${l.raw_material_sku}</td>
 <td>${l.warehouse_name}</td>
 <td>${l.current_stock}</td>
 <td>${l.reserved_stock}</td>
 <td><strong>${l.available_stock}</strong></td>
 <td>${l.unit}</td>
 <td>${l.reorder_level}</td>
 <td>${l.is_low_stock ? '<span class="status-badge low-stock">LOW STOCK</span>' : '<span class="status-badge ok-stock">OK</span>'}</td>
 ${isAdmin ? `<td><button onclick="window.adminAdjustStockLevel('${l.stock_level_id}','${(l.raw_material_name||'').replace(/'/g, "\\'").replace(/"/g, '&quot;')}',${l.current_stock},'rawmaterial','${(l.unit||'').replace(/'/g, "\\'")}')"
 style="background:#667eea;color:#fff;border:none;border-radius:4px;padding:4px 12px;cursor:pointer;font-size:12px;font-weight:600;">Edit</button></td>` : ''}
 </tr>
 `).join('')}
 </tbody>
 </table>
 </div>
 `;
 setStockDisplayArea(html);
 } catch (e) {
 notify(`Error: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // View stock analysis
 async function viewStockAnalysis() {
 try {
 setLoading(true);
 const res = await authedFetch('/api/stock-management/analysis');
 if (!res.ok) throw new Error('Failed to fetch stock analysis');
 const analysis = await res.json();
 
 let html = `
 <div class="stock-analysis-section">
 <h3>Stock Analysis Dashboard</h3>
 <div class="analysis-stats">
 <div class="stat-card">
 <h4>Product Items</h4>
 <p class="stat-number">${analysis.summary.total_product_items}</p>
 </div>
 <div class="stat-card">
 <h4>Raw Material Items</h4>
 <p class="stat-number">${analysis.summary.total_raw_material_items}</p>
 </div>
 <div class="stat-card warning">
 <h4>Low Stock Products</h4>
 <p class="stat-number">${analysis.summary.low_stock_products}</p>
 </div>
 <div class="stat-card warning">
 <h4>Low Stock Raw Materials</h4>
 <p class="stat-number">${analysis.summary.low_stock_raw_materials}</p>
 </div>
 <div class="stat-card danger">
 <h4>Damaged Items (30d)</h4>
 <p class="stat-number">${analysis.summary.damaged_items_30_days}</p>
 </div>
 <div class="stat-card info">
 <h4>Returns (30d)</h4>
 <p class="stat-number">${analysis.summary.returned_items_30_days}</p>
 </div>
 <div class="stat-card success">
 <h4>Total Product Value</h4>
 <p class="stat-number">${formatCurrency(analysis.summary.total_product_value)}</p>
 </div>
 <div class="stat-card success">
 <h4>Total Raw Material Value</h4>
 <p class="stat-number">${formatCurrency(analysis.summary.total_raw_material_value)}</p>
 </div>
 <div class="stat-card primary">
 <h4>Total Stock Value</h4>
 <p class="stat-number">${formatCurrency(analysis.summary.total_stock_value)}</p>
 </div>
 </div>
 </div>
 `;
 setStockDisplayArea(html);
 } catch (e) {
 notify(`Error: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }

 // UI render
 return (
 <div className="app-container">
 {/* Bulk Upload Modal */}
 {showBulkUpload && (
 <BulkUpload 
 module={showBulkUpload}
 onClose={() => setShowBulkUpload(null)}
 onSuccess={() => {
 fetchAllData();
 notify('Bulk upload completed successfully!', 'success');
 setShowBulkUpload(null);
 }}
 />
 )}

 {/* Offline Sync Status Bar - auto-collapses when online with no pending changes */}
 <div
 onMouseEnter={() => setSyncBarExpanded(true)}
 onMouseLeave={() => {
 if (offlineStatus.online && offlineStatus.pendingCount === 0 && !offlineStatus.syncing && !pullProgress) {
 setSyncBarExpanded(false);
 }
 }}
 style={{
 position: 'fixed', top: 0, left: 0, right: 0, zIndex: 9999,
 background: offlineStatus.online ? (offlineStatus.pendingCount > 0 ? '#f59e0b' : '#10b981') : '#ef4444',
 color: 'white',
 padding: syncBarExpanded ? '6px 16px' : '0 16px',
 display: 'flex', alignItems: 'center', justifyContent: 'space-between',
 fontSize: '12px', fontWeight: 600, gap: '12px',
 height: syncBarExpanded ? '32px' : '4px',
 minHeight: syncBarExpanded ? '32px' : '4px',
 overflow: 'hidden',
 transition: 'height 0.3s ease, min-height 0.3s ease, padding 0.3s ease, background 0.3s ease',
 cursor: syncBarExpanded ? 'default' : 'pointer'
 }}
 onClick={() => { if (!syncBarExpanded) setSyncBarExpanded(true); }}
 >
 <div style={{ display: 'flex', alignItems: 'center', gap: '10px', opacity: syncBarExpanded ? 1 : 0, transition: 'opacity 0.2s ease' }}>
 <span style={{ width: 8, height: 8, borderRadius: '50%', background: offlineStatus.online ? '#fff' : '#fca5a5', display: 'inline-block', animation: offlineStatus.syncing ? 'pulse 1s infinite' : 'none' }} />
 <span>{offlineStatus.online ? (offlineStatus.syncing ? 'SYNCING...' : 'ONLINE') : 'OFFLINE MODE'}</span>
 {offlineStatus.pendingCount > 0 && (
 <span style={{ background: 'rgba(0,0,0,0.2)', padding: '2px 8px', borderRadius: 10 }}>
 {offlineStatus.pendingCount} pending change{offlineStatus.pendingCount !== 1 ? 's' : ''}
 </span>
 )}
 {offlineStatus.lastFullSync && (
 <span style={{ opacity: 0.8 }}>Last sync: {new Date(offlineStatus.lastFullSync).toLocaleString()}</span>
 )}
 </div>
 <div style={{ display: 'flex', gap: '8px', opacity: syncBarExpanded ? 1 : 0, transition: 'opacity 0.2s ease' }}>
 {offlineStatus.online && offlineStatus.pendingCount > 0 && (
 <button onClick={handleSyncNow} disabled={offlineStatus.syncing} style={{
 padding: '3px 12px', background: 'rgba(255,255,255,0.25)', color: '#fff',
 border: '1px solid rgba(255,255,255,0.4)', borderRadius: 4, cursor: 'pointer', fontSize: '11px', fontWeight: 700
 }}>SYNC NOW</button>
 )}
 {offlineStatus.online && (
 <button onClick={handlePullFromCloud} disabled={offlineStatus.syncing || pullProgress !== null} style={{
 padding: '3px 12px', background: 'rgba(255,255,255,0.25)', color: '#fff',
 border: '1px solid rgba(255,255,255,0.4)', borderRadius: 4, cursor: 'pointer', fontSize: '11px', fontWeight: 700
 }}>{pullProgress ? `PULLING ${pullProgress.done}/${pullProgress.total}...` : 'PULL FROM CLOUD'}</button>
 )}
 </div>
 </div>

 {/* Dynamic spacer for sync bar */}
 <div style={{ height: syncBarExpanded ? '32px' : '4px', flexShrink: 0, transition: 'height 0.3s ease' }} />

 {/* Vertical Sidebar Navigation */}
 <aside className={`sidebar ${sidebarVisible ? 'visible' : 'hidden'}`}>
 <div className="sidebar-brand">
 <img 
 src="/company-logo.png" 
 alt="AstroBSM StockMaster" 
 className="sidebar-logo"
 onError={(e) => { e.target.style.display = 'none'; }}
 />
 <h1 className="sidebar-title">AstroBSM StockMaster</h1>
 <small className="build-badge">{BUILD_TAG}</small>
 </div>
 <nav className="sidebar-nav">
 {['dashboard','staff','attendance','salaryPayroll','products','rawMaterials','stockManagement','production','productionTasks','productionCompletions','consumables','machinesEquipment','transfers','returns','damagedTransfers','receiveTransfers','sales','customerOrders','customers','paymentTracking','procurement','logistics','marketing','hrCustomerCare','reports','financial','profits','sop','communication','announcements','training','userManagement','letters','geomap','regulatoryCompliance','networkWifi','settings'].filter(m => {
 // Letters is admin-only
 if (m === 'letters') return !currentUser || currentUser.role === 'admin';
 // Profits is admin-only
 if (m === 'profits') return !currentUser || currentUser.role === 'admin';
 // Announcements is admin-only
 if (m === 'announcements') return !currentUser || currentUser.role === 'admin';
 // Geomap is admin-only
 if (m === 'geomap') return !currentUser || currentUser.role === 'admin';
 // Regulatory Compliance is admin-only
 if (m === 'regulatoryCompliance') return !currentUser || currentUser.role === 'admin';
 // Network & WiFi Management is Super Admin only
 if (m === 'networkWifi') return !currentUser || currentUser.role === 'admin';
 // Admin always sees everything
 if (!currentUser || currentUser.role === 'admin') return true;
 // Dashboard always visible
 if (m === 'dashboard') return true;
 // customerOrders piggybacks on sales access
 if (m === 'customerOrders' && myModuleAccess && myModuleAccess['sales']) return true;
 // Use module access if available
 if (myModuleAccess && myModuleAccess[m] !== undefined) return myModuleAccess[m];
 // Default: hide for non-admin if no access data loaded yet
 return false;
 }).map(m => (
 <button key={m} className={`sidebar-btn ${activeModule===m?'active':''}`} onClick={() => { setActiveModule(m); setSidebarVisible(false); }} style={{position:'relative'}}>
 {m === 'rawMaterials' ? 'RAW MATERIALS' : m === 'stockManagement' ? 'STOCK MANAGEMENT' : m === 'productionTasks' ? 'PRODUCTION TASKS' : m === 'productionCompletions' ? 'PROD. COMPLETIONS' : m === 'consumables' ? 'CONSUMABLES' : m === 'machinesEquipment' ? 'MACHINES & EQUIPMENT' : m === 'transfers' ? 'TRANSFERS' : m === 'returns' ? 'RETURNED PRODUCTS' : m === 'damagedTransfers' ? 'DAMAGED TRANSFERS' : m === 'receiveTransfers' ? 'RECEIVE TRANSFERS' : m === 'paymentTracking' ? 'PAYMENTS & DEBT' : m === 'procurement' ? 'PROCUREMENT' : m === 'logistics' ? 'LOGISTICS' : m === 'marketing' ? 'MARKETER' : m === 'training' ? 'TRAINING ACADEMY' : m === 'profits' ? 'PROFITS' : m === 'hrCustomerCare' ? 'HR / CUSTOMER CARE' : m === 'userManagement' ? 'USER MANAGEMENT' : m === 'salaryPayroll' ? 'SALARY & PAYROLL' : m === 'customers' ? 'CUSTOMERS' : m === 'customerOrders' ? 'CUSTOMER ORDERS' : m === 'communication' ? 'COMMUNICATION' : m === 'letters' ? 'LETTERS' : m === 'sop' ? 'SOP / GMP' : m === 'geomap' ? 'GEOMAP' : m === 'regulatoryCompliance' ? 'REGULATORY COMPLIANCE' : m === 'networkWifi' ? 'NETWORK & WIFI' : m.toUpperCase()}
 {m === 'communication' && (commUnread.notices + commUnread.messages_total) > 0 && (
 <span style={{position:'absolute',top:'6px',right:'8px',background:'#ef4444',color:'#fff',fontSize:'10px',fontWeight:700,minWidth:'18px',height:'18px',borderRadius:'9px',display:'flex',alignItems:'center',justifyContent:'center',padding:'0 4px',animation:'badgePulse 2s infinite',boxShadow:'0 2px 6px rgba(239,68,68,0.5)'}}>
 {commUnread.notices + commUnread.messages_total > 99 ? '99+' : commUnread.notices + commUnread.messages_total}
 </span>
 )}
 </button>
 ))}
 </nav>
 <div className="sidebar-footer">
 <button className="btn btn-refresh" onClick={fetchAllData}>Refresh Data</button>
 <button className="btn btn-refresh" onClick={handlePullFromCloud} disabled={!offlineStatus.online || offlineStatus.syncing} style={{ marginTop: 4, background: offlineStatus.online ? '#2563eb' : '#94a3b8', fontSize: '11px' }}>
 {pullProgress ? `Syncing ${pullProgress.done}/${pullProgress.total}...` : 'Pull All Data for Offline'}
 </button>
 </div>
 </aside>

 {/* Sidebar Toggle Button */}
 <button 
 className="sidebar-toggle" 
 onClick={() => setSidebarVisible(!sidebarVisible)}
 title={sidebarVisible ? 'Hide Sidebar' : 'Show Sidebar'}
 >
 {sidebarVisible ? '<' : '>'}
 </button>

 {/* Main Content Area */}
 <div className={`main-wrapper ${sidebarVisible ? '' : 'expanded'}`}>

 {/* Notifications */}
 {notifications.length > 0 && (
 <div className="notifications-panel">
 {notifications.map(n => (
 <div key={n.id} className={`notification ${n.type}`}>
 <span>{n.message}</span>
 <button onClick={() => setNotifications((p)=>p.filter(x=>x.id!==n.id))}></button>
 </div>
 ))}
 </div>
 )}

 <main className="main">
 {/* Per-module Responsibilities & Duties panel (collapsible) */}
 <ModuleInfo module={activeModule} />
 {/* Dashboard */}
 {activeModule === 'dashboard' && (
 <div className="dashboard">
 <div className="dashboard-header">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Dashboard</h2>
 <p>Comprehensive overview for operations</p>
 </div>
 
 {/* Birthday Notifications */}
 {upcomingBirthdays.length > 0 && (
 <div className="birthday-notifications" style={{background:'linear-gradient(135deg,#fff9e6 0%,#fff3cd 100%)',borderRadius:12,padding:20,marginBottom:20,border:'1px solid #ffc107'}}>
 <h3 style={{margin:'0 0 12px 0',color:'#856404',fontSize:18}}>Upcoming Birthdays ({upcomingBirthdays.length})</h3>
 <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(280px,1fr))',gap:12}}>
 {upcomingBirthdays.map(birthday => (
 <div key={birthday.id || birthday.employee_id} className={`birthday-card ${birthday.is_today ? 'birthday-today' : ''}`} style={{background:birthday.is_today?'linear-gradient(135deg,#ff6b6b,#ee5a24)':'#fff',borderRadius:10,padding:14,boxShadow:'0 2px 8px rgba(0,0,0,0.08)',display:'flex',alignItems:'center',gap:12,color:birthday.is_today?'#fff':'#333'}}>
 <div style={{fontSize:32,lineHeight:1}}></div>
 <div>
 {birthday.is_today ? (
 <>
 <div style={{fontWeight:700,fontSize:15}}>Happy Birthday, {birthday.first_name} {birthday.last_name}!</div>
 <div style={{fontSize:13,opacity:0.9}}>Turning {birthday.age_turning} today</div>
 </>
 ) : (
 <>
 <div style={{fontWeight:700,fontSize:15}}>{birthday.first_name} {birthday.last_name}</div>
 <div style={{fontSize:13,color:'#666'}}>Birthday in {birthday.days_until} day{birthday.days_until > 1 ? 's' : ''} &middot; Age {birthday.age_turning}</div>
 </>
 )}
 </div>
 </div>
 ))}
 </div>
 </div>
 )}
 
 <div className="dashboard-stats">
 <div className="stat-card"><h3>Total Staff</h3><p className="stat-number">{data.staff.length}</p></div>
 <div className="stat-card"><h3>Products</h3><p className="stat-number">{data.products.length}</p></div>
 <div className="stat-card"><h3>Active Orders</h3><p className="stat-number">{(data.sales || []).filter(o=>o.status==='pending').length}</p></div>
 <div className="stat-card"><h3>Warehouses</h3><p className="stat-number">{data.warehouses.length}</p></div>
 </div>
 <div className="quick-actions">
 <h3>Quick Actions</h3>
 <div className="action-buttons">
 <button onClick={() => setActiveModule('sales')} className="action-btn">New Sales Order</button>
 <button onClick={() => setActiveModule('production')} className="action-btn">Production Console</button>
 <button onClick={() => setActiveModule('attendance')} className="action-btn">Clock In/Out</button>
 <button onClick={() => setActiveModule('paymentTracking')} className="action-btn" style={{background:'#e74c3c',color:'#fff'}}>Payments & Debt</button>
 <button onClick={() => setShowPriceList(true)} className="action-btn" style={{background:'#2ecc71',color:'#fff',fontWeight:700,fontSize:14}}>Product Price List</button>
 </div>
 </div>

 {/* ===================== PRODUCT PRICE LIST OVERLAY ===================== */}
 {showPriceList && (
 <div style={{position:'fixed',top:0,left:0,right:0,bottom:0,background:'rgba(0,0,0,0.6)',zIndex:10000,display:'flex',justifyContent:'center',alignItems:'flex-start',overflowY:'auto',padding:'20px 0'}}>
 <div style={{background:'#fff',width:'100%',maxWidth:900,borderRadius:12,boxShadow:'0 8px 32px rgba(0,0,0,0.3)',margin:'auto'}}>
 {/* Action Bar */}
 <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'16px 24px',borderBottom:'1px solid #eee',position:'sticky',top:0,background:'#fff',borderRadius:'12px 12px 0 0',zIndex:1}} className="price-list-no-print">
 <h3 style={{margin:0,fontSize:18,color:'#2c3e50'}}>Product Price List</h3>
 <div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
 <button onClick={() => {
 const link = document.createElement('a');
 link.href = '/api/products/price-list-pdf';
 link.download = 'Bonnesante_Price_List.pdf';
 document.body.appendChild(link);
 link.click();
 document.body.removeChild(link);
 notify('Downloading PDF...', 'info');
 }} className="btn" style={{background:'#e74c3c',color:'#fff',border:'none',padding:'8px 16px',borderRadius:6,fontWeight:600,cursor:'pointer'}}>Download PDF</button>
 <button onClick={() => {
 const printArea = document.getElementById('price-list-printable');
 if (!printArea) return;
 const win = window.open('', '_blank');
 if (!win) { notify('Popup blocked! Use Download PDF instead.', 'error'); return; }
 win.document.write(`<!DOCTYPE html><html><head><title>Bonnesante Medicals - Product Price List</title><style>
 @page { size: A4 portrait; margin: 15mm; }
 * { margin:0; padding:0; box-sizing:border-box; }
 body { font-family: 'Segoe UI', Arial, sans-serif; color: #2c3e50; padding: 0; }
 .header { text-align:center; padding:20px 0 15px; border-bottom:3px solid #2ecc71; margin-bottom:15px; }
 .header img { height:60px; margin-bottom:8px; }
 .header h1 { font-size:20px; color:#2c3e50; margin:4px 0; }
 .header p { font-size:11px; color:#888; }
 table { width:100%; border-collapse:collapse; font-size:11px; }
 th { background:#2ecc71; color:#fff; padding:8px 6px; text-align:left; font-size:10px; text-transform:uppercase; letter-spacing:0.5px; }
 td { padding:7px 6px; border-bottom:1px solid #eee; }
 tr:nth-child(even) { background:#f9fafb; }
 .price { font-weight:600; color:#2c3e50; }
 .footer { text-align:center; margin-top:20px; padding-top:15px; border-top:2px solid #eee; font-size:10px; color:#888; }
 .footer strong { color:#2c3e50; }
 </style></head><body>` + printArea.innerHTML + `</body></html>`);
 win.document.close();
 setTimeout(() => { win.print(); win.close(); }, 500);
 }} className="btn" style={{background:'#3498db',color:'#fff',border:'none',padding:'8px 16px',borderRadius:6,fontWeight:600,cursor:'pointer'}}>Print / Save PDF</button>
 <button onClick={() => {
 let text = '*BONNESANTE MEDICALS - PRODUCT PRICE LIST*\n';
 text += `Date: ${new Date().toLocaleDateString('en-NG',{day:'2-digit',month:'short',year:'numeric'})}\n`;
 text += '\n\n';
 const sortedProducts = [...(data.products||[])].sort((a,b)=>(a.name||'').localeCompare(b.name||''));
 sortedProducts.forEach((p, i) => {
 text += `*${i+1}. ${p.name}*`;
 if (p.sku) text += ` (${p.sku})`;
 text += '\n';
 if (p.pricing && p.pricing.length > 0) {
 p.pricing.forEach(pr => {
 text += ` Per ${pr.unit}:\n`;
 text += ` Retail: NGN ${Number(pr.retail_price||0).toLocaleString(undefined,{minimumFractionDigits:2})}\n`;
 text += ` Wholesale: NGN ${Number(pr.wholesale_price||0).toLocaleString(undefined,{minimumFractionDigits:2})}\n`;
 });
 } else {
 const retail = Number(p.retail_price || p.selling_price || 0);
 const wholesale = Number(p.wholesale_price || p.selling_price || 0);
 text += ` Retail: NGN ${retail.toLocaleString(undefined,{minimumFractionDigits:2})}\n`;
 text += ` Wholesale: NGN ${wholesale.toLocaleString(undefined,{minimumFractionDigits:2})}\n`;
 }
 text += '\n';
 });
 text += '\n';
 text += '*Bonnesante Medicals*\n';
 text += 'Tel: +234 707 679 3866 | +234 901 283 5413\n';
 text += 'Prices subject to change without notice.';
 const encoded = encodeURIComponent(text);
 window.open(`https://wa.me/?text=${encoded}`, '_blank');
 }} className="btn" style={{background:'#25D366',color:'#fff',border:'none',padding:'8px 16px',borderRadius:6,fontWeight:600,cursor:'pointer'}}>Share on WhatsApp</button>
 <button onClick={() => {
 let text = 'BONNESANTE MEDICALS - PRODUCT PRICE LIST\n';
 text += `Date: ${new Date().toLocaleDateString('en-NG',{day:'2-digit',month:'short',year:'numeric'})}\n\n`;
 const sortedProducts = [...(data.products||[])].sort((a,b)=>(a.name||'').localeCompare(b.name||''));
 sortedProducts.forEach((p, i) => {
 text += `${i+1}. ${p.name}`;
 if (p.pricing && p.pricing.length > 0) {
 p.pricing.forEach(pr => {
 text += ` | Per ${pr.unit}: Retail NGN ${Number(pr.retail_price||0).toLocaleString()} / Wholesale NGN ${Number(pr.wholesale_price||0).toLocaleString()}`;
 });
 } else {
 text += ` | Retail: NGN ${Number(p.retail_price||p.selling_price||0).toLocaleString()} | Wholesale: NGN ${Number(p.wholesale_price||p.selling_price||0).toLocaleString()}`;
 }
 text += '\n';
 });
 navigator.clipboard.writeText(text);
 notify('Price list copied to clipboard!', 'success');
 }} className="btn btn-secondary" style={{padding:'8px 16px',borderRadius:6,fontWeight:600}}>Copy Text</button>
 <button onClick={() => setShowPriceList(false)} className="btn" style={{background:'#e74c3c',color:'#fff',border:'none',padding:'8px 16px',borderRadius:6,fontWeight:600,cursor:'pointer'}}>Close</button>
 </div>
 </div>

 {/* Printable Content */}
 <div id="price-list-printable" style={{padding:'24px'}}>
 <div className="header" style={{textAlign:'center',paddingBottom:16,borderBottom:'3px solid #2ecc71',marginBottom:20}}>
 <img src="/company-logo.png" alt="" style={{height:60,marginBottom:8}} onError={(e)=>{e.target.style.display='none';}} />
 <h1 style={{fontSize:22,color:'#2c3e50',margin:'4px 0'}}>Bonnesante Medicals</h1>
 <p style={{fontSize:14,color:'#2ecc71',fontWeight:600,margin:'2px 0'}}>PRODUCT PRICE LIST</p>
 <p style={{fontSize:11,color:'#888'}}>Effective Date: {new Date().toLocaleDateString('en-NG', {day:'2-digit', month:'long', year:'numeric'})} | Prices in Nigerian Naira (NGN)</p>
 </div>

 <table style={{width:'100%',borderCollapse:'collapse',fontSize:13}}>
 <thead>
 <tr style={{background:'#2ecc71'}}>
 <th style={{padding:'10px 8px',color:'#fff',textAlign:'left',fontSize:11,textTransform:'uppercase',letterSpacing:0.5,borderBottom:'2px solid #27ae60'}}>#</th>
 <th style={{padding:'10px 8px',color:'#fff',textAlign:'left',fontSize:11,textTransform:'uppercase',letterSpacing:0.5,borderBottom:'2px solid #27ae60'}}>Product</th>
 <th style={{padding:'10px 8px',color:'#fff',textAlign:'left',fontSize:11,textTransform:'uppercase',letterSpacing:0.5,borderBottom:'2px solid #27ae60'}}>Unit</th>
 <th style={{padding:'10px 8px',color:'#fff',textAlign:'right',fontSize:11,textTransform:'uppercase',letterSpacing:0.5,borderBottom:'2px solid #27ae60'}}>Retail Price</th>
 <th style={{padding:'10px 8px',color:'#fff',textAlign:'right',fontSize:11,textTransform:'uppercase',letterSpacing:0.5,borderBottom:'2px solid #27ae60'}}>Wholesale Price</th>
 </tr>
 </thead>
 <tbody>
 {(() => {
 const sortedProducts = [...(data.products||[])].sort((a,b)=>(a.name||'').localeCompare(b.name||''));
 let rowNum = 0;
 return sortedProducts.map((p, idx) => {
 if (p.pricing && p.pricing.length > 0) {
 return p.pricing.map((pr, pi) => {
 rowNum++;
 return (
 <tr key={`${p.id}-${pi}`} style={{background: rowNum%2===0?'#f9fafb':'#fff'}}>
 <td style={{padding:'8px',borderBottom:'1px solid #eee',fontSize:12,color:'#888'}}>{rowNum}</td>
 <td style={{padding:'8px',borderBottom:'1px solid #eee',fontWeight:600}}>{p.name}{pi>0?` (${pr.unit})`:''}</td>
 <td style={{padding:'8px',borderBottom:'1px solid #eee',fontSize:12,color:'#666'}}>{pr.unit}</td>
 <td style={{padding:'8px',borderBottom:'1px solid #eee',textAlign:'right',fontWeight:600,color:'#e74c3c'}}>{formatCurrency(pr.retail_price)}</td>
 <td style={{padding:'8px',borderBottom:'1px solid #eee',textAlign:'right',fontWeight:600,color:'#f39c12'}}>{formatCurrency(pr.wholesale_price)}</td>
 </tr>
 );
 });
 } else {
 rowNum++;
 return (
 <tr key={p.id} style={{background: rowNum%2===0?'#f9fafb':'#fff'}}>
 <td style={{padding:'8px',borderBottom:'1px solid #eee',fontSize:12,color:'#888'}}>{rowNum}</td>
 <td style={{padding:'8px',borderBottom:'1px solid #eee',fontWeight:600}}>{p.name}</td>
 <td style={{padding:'8px',borderBottom:'1px solid #eee',fontSize:12,color:'#666'}}>{p.unit || 'each'}</td>
 <td style={{padding:'8px',borderBottom:'1px solid #eee',textAlign:'right',fontWeight:600,color:'#e74c3c'}}>{formatCurrency(p.retail_price || p.selling_price)}</td>
 <td style={{padding:'8px',borderBottom:'1px solid #eee',textAlign:'right',fontWeight:600,color:'#f39c12'}}>{formatCurrency(p.wholesale_price || p.selling_price)}</td>
 </tr>
 );
 }
 });
 })()}
 </tbody>
 </table>

 <div className="footer" style={{textAlign:'center',marginTop:24,paddingTop:16,borderTop:'2px solid #eee'}}>
 <p style={{fontSize:11,color:'#888',marginBottom:4}}><strong style={{color:'#2c3e50'}}>Bonnesante Medicals</strong> | Tel: +234 707 679 3866 | +234 901 283 5413</p>
 <p style={{fontSize:10,color:'#aaa'}}>Prices are subject to change without prior notice. All prices are in Nigerian Naira (NGN).</p>
 <p style={{fontSize:10,color:'#aaa',marginTop:2}}>Generated on {new Date().toLocaleString('en-NG')}</p>
 </div>
 </div>
 </div>
 </div>
 )}

 {/* Debtors Overview on Dashboard */}
 {ptDebtors.length > 0 && (
 <div style={{background:'#fff',borderRadius:8,padding:20,boxShadow:'0 1px 3px rgba(0,0,0,.1)',marginTop:20}}>
 <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16}}>
 <h3 style={{margin:0,color:'#e74c3c'}}>Customers Owing ({ptDebtors.length})</h3>
 <button className="btn btn-primary" onClick={() => setActiveModule('paymentTracking')} style={{fontSize:12}}>View Full Details</button>
 </div>
 <div className="table-responsive"><table className="data-table"><thead><tr><th>Customer</th><th>Phone</th><th>Total Owed</th><th>Total Paid</th><th>Balance</th><th>Overdue</th><th>Action</th></tr></thead><tbody>
 {ptDebtors.slice(0, 10).map(d => (
 <tr key={d.customer_id} style={{background: d.is_overdue ? '#fff5f5' : '#fff'}}>
 <td><strong>{d.customer_name}</strong></td>
 <td>{d.phone || '-'}</td>
 <td>{formatCurrency(d.total_invoiced)}</td>
 <td style={{color:'#27ae60'}}>{formatCurrency(d.total_paid)}</td>
 <td style={{color:'#e74c3c',fontWeight:700}}>{formatCurrency(d.balance)}</td>
 <td>{d.is_overdue ? <span style={{color:'#e74c3c',fontWeight:600}}>{d.days_overdue} days</span> : <span style={{color:'#27ae60'}}>No</span>}</td>
 <td><button className="btn btn-sm btn-secondary" onClick={() => { setActiveModule('paymentTracking'); setPtView('debtorDetail'); fetchPtDebtorDetail(d.customer_id); }}>View</button></td>
 </tr>
 ))}
 </tbody></table></div>
 {ptDebtors.length > 10 && <p style={{textAlign:'center',color:'#888',marginTop:8}}>Showing top 10 of {ptDebtors.length} debtors</p>}
 </div>
 )}
 </div>
 )}

 {/* Staff */}
 {activeModule === 'staff' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Staff Management</h2>
 </div>
 <div className="module-actions">
 <button onClick={() => setShowBulkUpload('staff')} className="btn btn-info">Bulk Upload</button>
 <button onClick={() => openForm('staff')} className="btn btn-primary">Add Staff</button>
 <button onClick={() => openForm('payroll')} className="btn btn-secondary">Process Payroll</button>
 </div>
 </div>

 <div className="table-container">
 <table className="data-table">
 <thead>
 <tr>
 <th>Employee ID</th><th>Name</th><th>Position</th><th>Clock PIN</th><th>Phone</th><th>Bank Details</th><th>Status</th><th>Actions</th>
 </tr>
 </thead>
 <tbody>
 {(data.staff || []).map(staff => (
 <tr key={staff.id}>
 <td>{staff.employee_id}</td>
 <td>{staff.first_name} {staff.last_name}</td>
 <td>{staff.position}</td>
 <td><strong>{staff.clock_pin}</strong></td>
 <td>{staff.phone || 'N/A'}</td>
 <td>{staff.bank_name}<br/><small>{staff.bank_account_number}</small></td>
 <td><span className={`status ${staff.is_active ? 'active' : 'inactive'}`}>{staff.is_active ? 'Active' : 'Inactive'}</span></td>
 <td className="actions">
 <button onClick={() => openForm('staff', staff)} className="btn-edit">Edit</button>
 <button onClick={() => openForm('payroll', { staff_id: staff.id })} className="btn-download">Payroll</button>
 <button onClick={() => deleteItem('staff', staff.id)} className="btn-delete">Delete</button>
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 </div>
 </div>
 )}

 {/* Attendance */}
 {activeModule === 'attendance' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Attendance Management</h2>
 </div>
 <button onClick={() => fetchData('attendance')} className="btn btn-secondary">Refresh</button>
 </div>
 
 {/* Clock PIN Input */}
 <div className="attendance-pin-section">
 <h3>Clock In/Out with PIN</h3>
 <div className="pin-input-container">
 <input 
 type="password" 
 placeholder="Enter your 4-digit Clock PIN" 
 maxLength="4"
 className="pin-input"
 id="clockPinInput"
 />
 <div className="pin-buttons">
 <button className="btn btn-success" onClick={() => handlePinClock('clock_in')}>Clock In</button>
 <button className="btn btn-warning" onClick={() => handlePinClock('clock_out')}>Clock Out</button>
 </div>
 </div>
 <p className="pin-hint">Enter your 4-digit PIN and click Clock In or Clock Out</p>
 </div>
 
 {/* Action Buttons */}
 <div className="attendance-actions">
 <button className="btn btn-primary" onClick={() => viewAttendanceStatus()}>View Status</button>
 <button className="btn btn-secondary" onClick={() => viewDetailedLog()}>Detailed Log</button>
 <button className="btn btn-success" onClick={() => viewBestPerformers()}>Best Performers</button>
 {(() => {
 const isAdmin = !currentUser || currentUser.role === 'admin';
 const fname = (currentUser?.first_name || '').toLowerCase();
 const lname = (currentUser?.last_name || '').toLowerCase();
 const uname = (currentUser?.username || '').toLowerCase();
 const email = (currentUser?.email || '').toLowerCase();
 const isArinze = fname.includes('arinze') || lname.includes('arinze') || uname.includes('arinze') || email.includes('arinze');
 const canBulkClockOut = isAdmin || isArinze;
 if (!canBulkClockOut) return null;
 return (
 <button
 className="btn btn-danger"
 style={{ background:'#c0392b', color:'#fff', borderColor:'#a82c1f' }}
 onClick={async () => {
 const activeNow = (data.attendance||[]).filter(a => a.clock_in && !a.clock_out).length;
 const msg = activeNow > 0
 ? `This will clock out ALL ${activeNow} currently clocked-in staff right now. Continue?`
 : 'No active clock-ins detected client-side. Run bulk clock-out anyway? (server will close any open records)';
 if (!window.confirm(msg)) return;
 try {
 setLoading && setLoading(true);
 const r = await authedFetch('/api/attendance/bulk-clock-out', {
 method: 'POST',
 headers: { 'Content-Type': 'application/json' },
 body: JSON.stringify({})
 });
 const d = await r.json();
 if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
 notify(`Bulk clock-out complete: ${d.closed_count} staff clocked out.`, 'success');
 fetchData('attendance');
 } catch(e) {
 notify(`Bulk clock-out failed: ${e.message}`, 'error');
 } finally {
 setLoading && setLoading(false);
 }
 }}
 title="Clock out ALL currently clocked-in staff (admin / production supervisor only)"
 >
 ⏱ Bulk Clock-Out All
 </button>
 );
 })()}
 </div>
 
 {/* Status Display Area */}
 <div id="attendanceDisplayArea" className="attendance-display-area"></div>
 </div>
 )}

 {/* ==================== SALARY & PAYROLL MODULE ==================== */}
 {activeModule === 'salaryPayroll' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Salary & Payroll</h2>
 </div>
 <div className="module-actions">
 <button className={`btn ${payrollTab === 'dashboard' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setPayrollTab('dashboard')}>Dashboard</button>
 <button className={`btn ${payrollTab === 'history' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setPayrollTab('history')}>Payroll History</button>
 <button className={`btn ${payrollTab === 'process' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setPayrollTab('process')}>Process Payroll</button>
 </div>
 </div>

 {/* Period Selector */}
 <div style={{display:'flex', gap:'1rem', alignItems:'center', padding:'0.75rem 1rem', background:'var(--gray-50, #f8f9fa)', borderRadius:8, marginBottom:'1rem', flexWrap:'wrap'}}>
 <label style={{fontWeight:600, fontSize:13}}>Pay Period:</label>
 <input type="date" value={payrollPeriod.start} onChange={(e) => setPayrollPeriod(p => ({...p, start: e.target.value}))} style={{padding:'6px 10px', borderRadius:6, border:'1px solid #ddd'}} />
 <span>to</span>
 <input type="date" value={payrollPeriod.end} onChange={(e) => setPayrollPeriod(p => ({...p, end: e.target.value}))} style={{padding:'6px 10px', borderRadius:6, border:'1px solid #ddd'}} />
 <button className="btn btn-primary" onClick={() => fetchPayrollDashboard(payrollPeriod.start, payrollPeriod.end)} style={{fontSize:12}}>Load Period</button>
 <button className="btn btn-secondary" onClick={() => {
 const now = new Date();
 const s = new Date(now.getFullYear(), now.getMonth(), 1).toISOString().split('T')[0];
 const e = new Date(now.getFullYear(), now.getMonth() + 1, 0).toISOString().split('T')[0];
 setPayrollPeriod({ start: s, end: e });
 fetchPayrollDashboard(s, e);
 }} style={{fontSize:12}}>Current Month</button>
 </div>

 {/* DASHBOARD TAB */}
 {payrollTab === 'dashboard' && (
 <div>
 {/* Summary Cards */}
 {payrollDashboard && (
 <div style={{display:'grid', gridTemplateColumns:'repeat(auto-fit, minmax(200px, 1fr))', gap:'1rem', marginBottom:'1.5rem'}}>
 <div style={{background:'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', color:'#fff', borderRadius:12, padding:'1.25rem', textAlign:'center'}}>
 <div style={{fontSize:28, fontWeight:700}}>N{(payrollDashboard.total_due || 0).toLocaleString('en-NG', {minimumFractionDigits:2})}</div>
 <div style={{fontSize:12, opacity:0.9, marginTop:4}}>Total Due Salaries</div>
 </div>
 <div style={{background:'linear-gradient(135deg, #11998e 0%, #38ef7d 100%)', color:'#fff', borderRadius:12, padding:'1.25rem', textAlign:'center'}}>
 <div style={{fontSize:28, fontWeight:700}}>{payrollDashboard.total_staff || 0}</div>
 <div style={{fontSize:12, opacity:0.9, marginTop:4}}>Active Staff</div>
 </div>
 <div style={{background:'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)', color:'#fff', borderRadius:12, padding:'1.25rem', textAlign:'center'}}>
 <div style={{fontSize:28, fontWeight:700}}>{(payrollDashboard.total_hours || 0).toFixed(1)}</div>
 <div style={{fontSize:12, opacity:0.9, marginTop:4}}>Total Hours Worked</div>
 </div>
 <div style={{background:'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)', color:'#fff', borderRadius:12, padding:'1.25rem', textAlign:'center'}}>
 <div style={{fontSize:28, fontWeight:700}}>{payrollDashboard.period_start} - {payrollDashboard.period_end}</div>
 <div style={{fontSize:12, opacity:0.9, marginTop:4}}>Current Period</div>
 </div>
 </div>
 )}

 {/* Staff Salary Table */}
 <div className="table-container">
 <table className="data-table">
 <thead>
 <tr>
 <th>Employee ID</th>
 <th>Name</th>
 <th>Position</th>
 <th>Pay Mode</th>
 <th>Rate</th>
 <th>Hours Worked</th>
 <th>Days</th>
 <th>Overtime</th>
 <th>Gross Pay (N)</th>
 <th>Status</th>
 <th>Actions</th>
 </tr>
 </thead>
 <tbody>
 {(payrollDashboard?.staff || []).map(s => (
 <tr key={s.staff_id} style={{background: s.payroll_status === 'paid' ? '#e8f5e9' : s.payroll_status === 'approved' ? '#e3f2fd' : s.payroll_status === 'draft' ? '#fff8e1' : 'transparent'}}>
 <td><strong>{s.employee_id}</strong></td>
 <td>{s.first_name} {s.last_name}</td>
 <td>{s.position}</td>
 <td><span style={{background: s.payment_mode === 'hourly' ? '#e3f2fd' : '#f3e5f5', padding:'2px 8px', borderRadius:12, fontSize:11, fontWeight:600}}>{s.payment_mode === 'hourly' ? 'Hourly' : 'Monthly'}</span></td>
 <td>{s.payment_mode === 'hourly' ? `N${s.hourly_rate.toLocaleString()}/hr` : `N${s.monthly_salary.toLocaleString()}/mo`}</td>
 <td>{s.hours_worked.toFixed(1)}</td>
 <td>{s.days_worked}</td>
 <td style={{color: s.overtime_hours > 0 ? '#e65100' : 'inherit', fontWeight: s.overtime_hours > 0 ? 600 : 400}}>{s.overtime_hours.toFixed(1)}</td>
 <td><strong style={{color:'#1b5e20'}}>N{s.gross_pay.toLocaleString('en-NG', {minimumFractionDigits:2})}</strong></td>
 <td>
 <span style={{
 padding:'3px 10px', borderRadius:12, fontSize:11, fontWeight:600,
 background: s.payroll_status === 'paid' ? '#c8e6c9' : s.payroll_status === 'approved' ? '#bbdefb' : s.payroll_status === 'draft' ? '#fff9c4' : '#f5f5f5',
 color: s.payroll_status === 'paid' ? '#2e7d32' : s.payroll_status === 'approved' ? '#1565c0' : s.payroll_status === 'draft' ? '#f57f17' : '#757575'
 }}>
 {s.payroll_status === 'not_processed' ? 'Pending' : s.payroll_status.toUpperCase()}
 </span>
 </td>
 <td className="actions" style={{whiteSpace:'nowrap'}}>
 {s.payroll_status === 'not_processed' && (
 <button className="btn-edit" onClick={() => processSinglePayroll(s.staff_id)} disabled={payrollProcessing} style={{fontSize:11}}>Process</button>
 )}
 {s.payroll_id && (
 <button className="btn-download" onClick={() => downloadPayslipPdf(s.payroll_id, `${s.employee_id}_${s.first_name}`)} style={{fontSize:11}}>Payslip PDF</button>
 )}
 {s.payroll_id && s.payroll_status === 'draft' && (
 <button className="btn-edit" onClick={() => updatePayrollEntryStatus(s.payroll_id, 'approved')} style={{fontSize:11, background:'#1565c0', color:'#fff'}}>Approve</button>
 )}
 {s.payroll_id && s.payroll_status === 'approved' && (
 <button className="btn-edit" onClick={() => updatePayrollEntryStatus(s.payroll_id, 'paid')} style={{fontSize:11, background:'#2e7d32', color:'#fff'}}>Mark Paid</button>
 )}
 </td>
 </tr>
 ))}
 {(!payrollDashboard?.staff || payrollDashboard.staff.length === 0) && (
 <tr><td colSpan="11" style={{textAlign:'center', padding:'2rem', color:'#999'}}>No staff data. Click "Load Period" to fetch salary data.</td></tr>
 )}
 </tbody>
 </table>
 </div>

 {/* Bank Details Summary */}
 {payrollDashboard?.staff?.length > 0 && (
 <div style={{marginTop:'1.5rem'}}>
 <h3 style={{marginBottom:'0.75rem', fontSize:15}}>Bank Payment Summary</h3>
 <div className="table-container">
 <table className="data-table">
 <thead>
 <tr><th>Employee</th><th>Bank</th><th>Account Name</th><th>Account Number</th><th>Amount (N)</th></tr>
 </thead>
 <tbody>
 {payrollDashboard.staff.filter(s => s.gross_pay > 0).map(s => (
 <tr key={s.staff_id}>
 <td>{s.first_name} {s.last_name}</td>
 <td>{s.bank_name || 'N/A'}</td>
 <td>{s.bank_account_name || 'N/A'}</td>
 <td>{s.bank_account_number || 'N/A'}</td>
 <td><strong>N{s.net_pay.toLocaleString('en-NG', {minimumFractionDigits:2})}</strong></td>
 </tr>
 ))}
 </tbody>
 </table>
 </div>
 </div>
 )}
 </div>
 )}

 {/* HISTORY TAB */}
 {payrollTab === 'history' && (
 <div>
 <div style={{display:'flex', gap:'0.5rem', marginBottom:'1rem'}}>
 <button className="btn btn-secondary" onClick={fetchPayrollEntries} style={{fontSize:12}}>Refresh</button>
 </div>
 <div className="table-container">
 <table className="data-table">
 <thead>
 <tr>
 <th>Date</th>
 <th>Employee</th>
 <th>Position</th>
 <th>Period</th>
 <th>Regular Hrs</th>
 <th>OT Hrs</th>
 <th>Gross (N)</th>
 <th>Deductions (N)</th>
 <th>Net Pay (N)</th>
 <th>Status</th>
 <th>Actions</th>
 </tr>
 </thead>
 <tbody>
 {payrollEntries.map(entry => (
 <tr key={entry.id} style={{background: entry.status === 'paid' ? '#e8f5e9' : entry.status === 'approved' ? '#e3f2fd' : 'transparent'}}>
 <td style={{fontSize:11}}>{new Date(entry.created_at).toLocaleDateString()}</td>
 <td><strong>{entry.employee_id}</strong> - {entry.staff_name}</td>
 <td>{entry.position}</td>
 <td style={{fontSize:11}}>{entry.pay_period_start} to {entry.pay_period_end}</td>
 <td>{entry.regular_hours.toFixed(1)}</td>
 <td>{entry.overtime_hours.toFixed(1)}</td>
 <td>N{entry.gross_pay.toLocaleString('en-NG', {minimumFractionDigits:2})}</td>
 <td>N{entry.deductions.toLocaleString('en-NG', {minimumFractionDigits:2})}</td>
 <td><strong style={{color:'#1b5e20'}}>N{entry.net_pay.toLocaleString('en-NG', {minimumFractionDigits:2})}</strong></td>
 <td>
 <span style={{
 padding:'3px 10px', borderRadius:12, fontSize:11, fontWeight:600,
 background: entry.status === 'paid' ? '#c8e6c9' : entry.status === 'approved' ? '#bbdefb' : '#fff9c4',
 color: entry.status === 'paid' ? '#2e7d32' : entry.status === 'approved' ? '#1565c0' : '#f57f17'
 }}>{entry.status.toUpperCase()}</span>
 </td>
 <td className="actions" style={{whiteSpace:'nowrap'}}>
 <button className="btn-download" onClick={() => downloadPayslipPdf(entry.id, `${entry.employee_id}_${entry.staff_name}`)} style={{fontSize:11}}>PDF</button>
 {entry.status === 'draft' && (
 <button className="btn-edit" onClick={() => updatePayrollEntryStatus(entry.id, 'approved')} style={{fontSize:11}}>Approve</button>
 )}
 {entry.status === 'approved' && (
 <button className="btn-edit" onClick={() => updatePayrollEntryStatus(entry.id, 'paid')} style={{fontSize:11, background:'#2e7d32', color:'#fff'}}>Paid</button>
 )}
 </td>
 </tr>
 ))}
 {payrollEntries.length === 0 && (
 <tr><td colSpan="11" style={{textAlign:'center', padding:'2rem', color:'#999'}}>No payroll entries yet. Process payroll from the Dashboard or Process tab.</td></tr>
 )}
 </tbody>
 </table>
 </div>
 </div>
 )}

 {/* PROCESS TAB */}
 {payrollTab === 'process' && (
 <div>
 <div style={{background:'#fff', borderRadius:12, padding:'2rem', boxShadow:'0 2px 8px rgba(0,0,0,0.08)', maxWidth:600}}>
 <h3 style={{marginBottom:'1rem'}}>Bulk Payroll Processing</h3>
 <p style={{color:'#666', fontSize:13, marginBottom:'1.5rem'}}>
 Process payroll for all active staff members for the selected pay period. This calculates gross pay based on each staff member's configured payment mode (hourly or monthly), attendance hours, and overtime.
 </p>
 <div style={{background:'#f8f9fa', padding:'1rem', borderRadius:8, marginBottom:'1rem'}}>
 <div style={{fontSize:13}}><strong>Period:</strong> {payrollPeriod.start} to {payrollPeriod.end}</div>
 <div style={{fontSize:13, marginTop:4}}><strong>Active Staff:</strong> {payrollDashboard?.total_staff || '...'}</div>
 <div style={{fontSize:13, marginTop:4}}><strong>Estimated Total:</strong> N{(payrollDashboard?.total_due || 0).toLocaleString('en-NG', {minimumFractionDigits:2})}</div>
 </div>
 <button 
 className="btn btn-primary" 
 onClick={bulkProcessPayroll} 
 disabled={payrollProcessing}
 style={{width:'100%', padding:'12px', fontSize:15, fontWeight:600}}
 >
 {payrollProcessing ? 'Processing...' : 'Process Payroll for All Staff'}
 </button>
 <p style={{color:'#999', fontSize:11, marginTop:'0.75rem', textAlign:'center'}}>
 Staff with already-processed payroll for this period will be skipped automatically.
 </p>
 </div>

 {/* Individual Processing */}
 <div style={{marginTop:'2rem'}}>
 <h3 style={{marginBottom:'0.75rem'}}>Individual Payroll</h3>
 <p style={{color:'#666', fontSize:13, marginBottom:'1rem'}}>Process payroll for a single staff member and auto-download their payslip.</p>
 <div className="table-container">
 <table className="data-table">
 <thead>
 <tr><th>Employee ID</th><th>Name</th><th>Position</th><th>Pay Mode</th><th>Rate</th><th>Status</th><th>Action</th></tr>
 </thead>
 <tbody>
 {(payrollDashboard?.staff || []).map(s => (
 <tr key={s.staff_id}>
 <td>{s.employee_id}</td>
 <td>{s.first_name} {s.last_name}</td>
 <td>{s.position}</td>
 <td>{s.payment_mode === 'hourly' ? 'Hourly' : 'Monthly'}</td>
 <td>{s.payment_mode === 'hourly' ? `N${s.hourly_rate.toLocaleString()}/hr` : `N${s.monthly_salary.toLocaleString()}/mo`}</td>
 <td>
 <span style={{
 padding:'3px 10px', borderRadius:12, fontSize:11, fontWeight:600,
 background: s.payroll_status === 'paid' ? '#c8e6c9' : s.payroll_status === 'approved' ? '#bbdefb' : s.payroll_status === 'draft' ? '#fff9c4' : '#f5f5f5',
 color: s.payroll_status === 'paid' ? '#2e7d32' : s.payroll_status === 'approved' ? '#1565c0' : s.payroll_status === 'draft' ? '#f57f17' : '#757575'
 }}>{s.payroll_status === 'not_processed' ? 'Pending' : s.payroll_status.toUpperCase()}</span>
 </td>
 <td>
 {s.payroll_status === 'not_processed' ? (
 <button className="btn btn-primary" onClick={() => processSinglePayroll(s.staff_id)} disabled={payrollProcessing} style={{fontSize:11, padding:'4px 12px'}}>
 {payrollProcessing ? '...' : 'Process & Download'}
 </button>
 ) : (
 <button className="btn-download" onClick={() => downloadPayslipPdf(s.payroll_id, `${s.employee_id}_${s.first_name}`)} style={{fontSize:11}}>Download Payslip</button>
 )}
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 </div>
 </div>
 </div>
 )}
 </div>
 )}

 {/* Products */}
 {activeModule === 'products' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Products</h2>
 </div>
 <div className="module-actions">
 <button onClick={() => setShowBulkUpload('products')} className="btn btn-info">Bulk Upload</button>
 <button onClick={() => openForm('product')} className="btn btn-primary">Add Product</button>
 </div>
 </div>
 <div className="table-container">
 <table className="data-table">
 <thead><tr><th>Name</th><th>Units Available</th><th>Description</th><th>Actions</th></tr></thead>
 <tbody>
 {(data.products || []).map(product => (
 <tr key={product.id}>
 <td>{product.name}</td>
 <td>{(product.pricing && product.pricing.length > 0)
 ? product.pricing.map(p => p.unit).join(', ')
 : ((product.selling_price || product.retail_price || product.wholesale_price) ? 'each' : 'No units set')}</td>
 <td>{product.description}</td>
 <td className="actions">
 <button onClick={() => openForm('product', product)} className="btn-edit">Edit</button>
 <button onClick={() => openForm('stockIntake', { product_id: product.id })} className="btn-add">Stock Intake</button>
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 </div>
 </div>
 )}

 {/* Raw Materials */}
 {activeModule === 'rawMaterials' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Raw Materials</h2>
 </div>
 <div className="module-actions">
 <button onClick={() => setShowBulkUpload('rawMaterials')} className="btn btn-info">Bulk Upload</button>
 <button onClick={() => openForm('rawMaterial')} className="btn btn-primary">+ Add Raw Material</button>
 <button onClick={() => openForm('rawMaterialIntake')} className="btn btn-secondary">Stock Intake</button>
 </div>
 </div>
 
 <div className="data-section">
 <h3>Raw Materials Inventory</h3>
 <table className="data-table">
 <thead>
 <tr>
 <th>Name</th>
 <th>SKU</th>
 <th>Current Stock</th>
 <th>Unit</th>
 <th>Reorder Level</th>
 <th>Cost per Unit</th>
 <th>Actions</th>
 </tr>
 </thead>
 <tbody>
 {(data.rawMaterials || []).map(material => (
 <tr key={material.id}>
 <td>{material.name}</td>
 <td>{material.sku}</td>
 <td className={material.current_stock <= material.reorder_level ? 'low-stock' : ''}>
 {material.current_stock || 0}
 </td>
 <td>{material.unit || 'kg'}</td>
 <td>{material.reorder_level || 0}</td>
 <td>₦{parseFloat(material.unit_cost || 0).toLocaleString('en-NG', {minimumFractionDigits: 2})}</td>
 <td className="actions">
 <button onClick={() => openForm('rawMaterial', material)} className="btn-edit">Edit</button>
 <button onClick={() => openForm('rawMaterialIntake', { raw_material_id: material.id })} className="btn-add">Stock</button>
 <button onClick={() => deleteItem('rawMaterial', material.id)} className="btn-delete">Delete</button>
 </td>
 </tr>
 ))}
 {(!data.rawMaterials || data.rawMaterials.length === 0) && (
 <tr>
 <td colSpan="7" className="no-data">No raw materials found. Click "Add Raw Material" to get started.</td>
 </tr>
 )}
 </tbody>
 </table>
 </div>
 </div>
 )}

 {/* Production */}
 {activeModule === 'production' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Production</h2>
 </div>
 <div className="module-header-actions">
 <button onClick={() => setShowBulkUpload('bom')} className="btn btn-info">Bulk Upload BOM</button>
 <button onClick={() => { setShowForm('bom'); setEditingBomProductId(null); setBomLines([{ id: Date.now(), raw_material_id: '', qty_per_unit: '', unit: 'kg' }]); }} className="btn btn-secondary">Register BOM</button>
 <button onClick={() => setShowForm('productionOrder')} className="btn btn-primary">+ New Production Order</button>
 </div>
 </div>
 
 {/* Production Console with Requirements Calculator */}
 <div className="production-console">
 <h3> Production Console - Material Requirements Calculator</h3>
 <div className="console-form">
 <div className="form-row">
 <div className="form-group">
 <label>Select Product *</label>
 <select value={forms.production.product_id} onChange={(e)=>{ handleFormChange('production','product_id',e.target.value); setProductionRequirements(null); }}>
 <option value="">Choose Product</option>
 {(data.products||[]).map(p => (<option key={p.id} value={p.id}>{p.name} - {p.sku}</option>))}
 </select>
 </div>
 <div className="form-group">
 <label>Quantity to Produce *</label>
 <input type="number" step="0.01" value={forms.production.quantity_to_produce} onChange={(e)=>{ handleFormChange('production','quantity_to_produce',e.target.value); setProductionRequirements(null); }} placeholder="Enter quantity"/>
 </div>
 <div className="form-group">
 <label>Warehouse *</label>
 <select value={forms.production.warehouse_id} onChange={(e)=>{ handleFormChange('production','warehouse_id',e.target.value); setProductionRequirements(null); }}>
 <option value="">Select Warehouse</option>
 {(data.warehouses||[]).map(w => (<option key={w.id} value={w.id}>{w.name}</option>))}
 </select>
 </div>
 </div>
 <div className="form-row">
 <div className="form-group">
 <button 
 className="btn btn-info" 
 disabled={!forms.production.product_id || !forms.production.quantity_to_produce || !forms.production.warehouse_id}
 onClick={calculateProductionRequirements}
 style={{ marginRight: '10px' }}
 >
 Calculate Requirements
 </button>
 {forms.production.product_id && (
 <button 
 className="btn btn-secondary" 
 onClick={() => viewProductBOM(forms.production.product_id)}
 >
 View BOM
 </button>
 )}
 </div>
 </div>
 </div>
 
 {/* Requirements Display */}
 {productionRequirements && (
 <div className="requirements-display">
 <h4>Material Requirements for {productionRequirements.quantity_to_produce} units of {productionRequirements.product_name}</h4>
 
 <div className="requirements-summary">
 <div className="summary-card">
 <span className="label">Total Material Cost:</span>
 <span className="value">{formatCurrency(productionRequirements.total_material_cost)}</span>
 </div>
 <div className="summary-card">
 <span className="label">Cost per Unit:</span>
 <span className="value">{formatCurrency(productionRequirements.cost_per_unit)}</span>
 </div>
 <div className={`summary-card ${productionRequirements.can_produce ? 'success' : 'error'}`}>
 <span className="label">Production Status:</span>
 <span className="value">{productionRequirements.can_produce ? ' CAN PRODUCE' : ' INSUFFICIENT STOCK'}</span>
 </div>
 </div>
 
 {productionRequirements.shortages && productionRequirements.shortages.length > 0 && (
 <div className="shortages-section">
 <h5> Stock Shortages Detected</h5>
 <table className="stock-table">
 <thead>
 <tr>
 <th>Raw Material</th>
 <th>Required</th>
 <th>Available</th>
 <th>Shortage</th>
 </tr>
 </thead>
 <tbody>
 {productionRequirements.shortages.map((shortage, idx) => (
 <tr key={idx} className="shortage-row">
 <td>{shortage.raw_material_name}</td>
 <td>{shortage.required}</td>
 <td>{shortage.available}</td>
 <td className="text-danger"><strong>{shortage.shortage}</strong></td>
 </tr>
 ))}
 </tbody>
 </table>
 </div>
 )}
 
 <table className="stock-table">
 <thead>
 <tr>
 <th>Raw Material</th>
 <th>SKU</th>
 <th>Qty per Unit</th>
 <th>Required Qty</th>
 <th>Unit</th>
 <th>Unit Cost</th>
 <th>Line Cost</th>
 <th>Available Stock</th>
 <th>Status</th>
 </tr>
 </thead>
 <tbody>
 {productionRequirements.requirements.map((req, idx) => (
 <tr key={idx} className={req.sufficient_stock === false ? 'low-stock-row' : ''}>
 <td>{req.raw_material_name}</td>
 <td>{req.raw_material_sku}</td>
 <td>{req.qty_per_unit}</td>
 <td><strong>{req.required_quantity}</strong></td>
 <td>{req.unit}</td>
 <td>{formatCurrency(req.unit_cost)}</td>
 <td>{formatCurrency(req.line_cost)}</td>
 <td>{req.available_stock !== null ? req.available_stock : 'N/A'}</td>
 <td>
 {req.sufficient_stock === true && <span className="status-badge ok-stock"> OK</span>}
 {req.sufficient_stock === false && <span className="status-badge low-stock"> INSUFFICIENT</span>}
 </td>
 </tr>
 ))}
 </tbody>
 </table> 
 <div className="approval-section">
 <button 
 className="btn btn-success btn-large"
 onClick={approveProductionAndDeduct}
 disabled={!productionRequirements.can_produce}
 style={{ opacity: productionRequirements.can_produce ? 1 : 0.5 }}
 >
 Approve Production & Deduct Raw Materials from Stock
 </button>
 <button 
 className="btn btn-secondary btn-large"
 onClick={() => {
 setProductionRequirements(null);
 notify('Production calculation cancelled', 'info');
 }}
 style={{ marginLeft: '10px' }}
 >
 Cancel
 </button>
 {productionRequirements.can_produce && (
 <p className="approval-note">
 This will deduct the required raw materials from <strong>{(data.warehouses||[]).find(w => w.id === productionRequirements.warehouse_id)?.name}</strong> inventory and create stock movement records.
 </p>
 )}
 {!productionRequirements.can_produce && (
 <p className="approval-note text-danger">
 Production cannot be approved due to insufficient stock. Please resolve shortages above or cancel this calculation.
 </p>
 )}
 </div>
 </div>
 )}
 
 {/* Stock Display Area for BOM viewing */}
 {stockDisplayArea && (
 <div className="stock-display-area" dangerouslySetInnerHTML={{ __html: stockDisplayArea }}></div>
 )}
 </div>

 {/* Registered BOMs Table */}
 <div className="table-container">
 <h3>Registered Bills of Materials ({allBoms.length})</h3>
 {allBoms.length === 0 ? (
 <p style={{padding:'15px',color:'#888'}}>No BOMs registered yet. Click "Register BOM" to create one.</p>
 ) : (
 <table className="data-table">
 <thead>
 <tr>
 <th>Product</th>
 <th>SKU</th>
 <th>Raw Materials</th>
 <th>Total Cost/Unit</th>
 <th>Created</th>
 <th>Actions</th>
 </tr>
 </thead>
 <tbody>
 {allBoms.map(bom => (
 <tr key={bom.bom_id}>
 <td>{bom.product_name}</td>
 <td>{bom.product_sku}</td>
 <td>{bom.raw_materials_count}</td>
 <td>{formatCurrency(bom.total_material_cost)}</td>
 <td>{bom.bom_created_at ? new Date(bom.bom_created_at).toLocaleDateString() : 'N/A'}</td>
 <td className="actions">
 <button onClick={() => viewProductBOM(bom.product_id)} className="btn-edit" style={{marginRight:4}}>View</button>
 <button onClick={() => editBOM(bom.product_id)} className="btn-edit" style={{background:'#e67e22',marginRight:4}}>Edit</button>
 <button onClick={() => deleteBOM(bom.product_id, bom.product_name)} className="btn-edit" style={{background:'#e74c3c'}}>Delete</button>
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 )}
 </div>

 <div className="table-container">
 <h3>Production Orders</h3>
 <table className="data-table">
 <thead><tr><th>Order</th><th>Product</th><th>Planned Qty</th><th>Status</th><th>Actions</th></tr></thead>
 <tbody>
 {(data.production||[]).map(order => (
 <tr key={order.id}>
 <td>{order.order_number || order.id}</td>
 <td>{order.product_name || order.product_id}</td>
 <td>{order.quantity_planned || order.quantity_to_produce}</td>
 <td><span className={`status ${order.status}`}>{order.status}</span></td>
 <td className="actions"><button onClick={() => openForm('productionOrder', order)} className="btn-edit">Edit</button></td>
 </tr>
 ))}
 </tbody>
 </table>
 </div>
 </div>
 )}

 {/* Sales */}
 {activeModule === 'sales' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Sales Orders</h2>
 </div>
 <button onClick={() => openForm('salesOrder')} className="btn btn-primary">+ New Sales Order</button>
 <button onClick={() => { setActiveModule('logistics'); }} className="btn btn-secondary" style={{marginLeft:8}}>Manifest</button>
 </div>
 {/* Customer-facing public order link (share with customers to let them place orders) */}
 <div style={{
   display:'flex', alignItems:'center', gap:10, flexWrap:'wrap',
   background:'#eef5ff', border:'1px solid #b6d4fe', borderRadius:10,
   padding:'10px 14px', margin:'8px 0 14px'
 }}>
   <span style={{fontWeight:600, color:'#0a3d62'}}>Customer Order Link:</span>
   <input
     type="text"
     readOnly
     value={`${window.location.origin}/order.html`}
     onFocus={(e) => e.target.select()}
     style={{
       flex:'1 1 280px', minWidth:240, padding:'8px 10px',
       border:'1px solid #b6d4fe', borderRadius:6, background:'#fff',
       fontFamily:'monospace', fontSize:13, color:'#1d2433'
     }}
   />
   <button
     className="btn btn-primary"
     onClick={async () => {
       const url = `${window.location.origin}/order.html`;
       try {
         if (navigator.clipboard && navigator.clipboard.writeText) {
           await navigator.clipboard.writeText(url);
         } else {
           const ta = document.createElement('textarea');
           ta.value = url; document.body.appendChild(ta); ta.select();
           document.execCommand('copy'); document.body.removeChild(ta);
         }
         notify('Customer order link copied to clipboard', 'success');
       } catch (err) {
         notify('Could not copy automatically — please copy manually', 'error');
       }
     }}
     title="Copy link to clipboard"
   >Copy Link</button>
   <a
     href="/order.html"
     target="_blank"
     rel="noopener noreferrer"
     className="btn btn-secondary"
     title="Preview the customer order page"
   >Preview</a>
   <button
     className="btn btn-secondary"
     onClick={() => {
       const url = `${window.location.origin}/order.html`;
       const text = encodeURIComponent(`Hello, you can place your order with BONNESANTE MEDICALS here: ${url}`);
       window.open(`https://wa.me/?text=${text}`, '_blank', 'noopener');
     }}
     title="Share via WhatsApp"
   >Share on WhatsApp</button>
 </div>
 <div className="table-container">
 <table className="data-table">
 <thead><tr><th>Order #</th><th>Customer</th><th>Warehouse</th><th>Total</th><th>Status</th><th>Payment</th><th>Actions</th></tr></thead>
 <tbody>
 {(data.sales||[]).map(order => {
 const warehouse = (data.warehouses||[]).find(w => w.id === order.warehouse_id);
 const customer = (data.customers||[]).find(c => c.id === order.customer_id);
 return (
 <tr key={order.id}>
 <td>{order.order_number || `#${order.id.substring(0,8)}`}</td>
 <td>{order.customer_name || (customer ? customer.name : order.customer_id)}</td>
 <td>{warehouse ? warehouse.name : 'N/A'}</td>
 <td>{formatCurrency(order.total_amount)}</td>
 <td><span className={`status ${order.status}`}>{order.status}</span></td>
 <td><span className={`status ${order.payment_status || 'unpaid'}`}>{order.payment_status || 'unpaid'}</span></td>
 <td className="actions">
 {order.payment_status === 'unpaid' ? (
 <>
 <button onClick={() => createInvoiceFromOrder(order.id)} className="btn btn-primary" title="Generate Invoice for Payment Tracking" style={{background:'#8e44ad',color:'#fff',border:'none',marginRight:4}}>Generate Invoice</button>
 <button onClick={() => markOrderAsPaid(order.id)} className="btn btn-success" title="Mark as Paid & Generate Receipt">Mark Paid</button>
 <button onClick={() => downloadInvoice(order.id)} className="btn btn-secondary" title="Download Invoice">Invoice PDF</button>
 <button onClick={() => printInvoiceThermal(order.id)} className="btn btn-primary" title="Print Invoice (Thermal)" style={{background:'#e67e22',color:'#fff',border:'none'}}>Print</button>
 </>
 ) : order.payment_status === 'partial' ? (
 <>
 <button onClick={() => createInvoiceFromOrder(order.id)} className="btn btn-primary" title="View/Create Invoice" style={{background:'#8e44ad',color:'#fff',border:'none',marginRight:4}}>Track Payments</button>
 <button onClick={() => markOrderAsPaid(order.id)} className="btn btn-success" title="Mark as Fully Paid">Mark Paid</button>
 <button onClick={() => downloadReceipt(order.id)} className="btn btn-primary" title="Download Receipt">Receipt</button>
 <button onClick={() => printReceiptThermal(order.id)} className="btn btn-primary" title="Print Receipt (Thermal)" style={{background:'#e67e22',color:'#fff',border:'none'}}>Print</button>
 </>
 ) : (
 <>
 <button onClick={() => downloadReceipt(order.id)} className="btn btn-primary" title="Download Receipt">Receipt</button>
 <button onClick={() => printReceiptThermal(order.id)} className="btn btn-primary" title="Print Receipt (Thermal)" style={{background:'#e67e22',color:'#fff',border:'none'}}>Print</button>
 </>
 )}}
 <button onClick={() => processOrder(order.id)} className="btn-paid">Process</button>
 {(!currentUser || currentUser.role === 'admin') && (
 <button onClick={() => deleteSalesOrder(order.id)} className="btn btn-danger" title="Delete Order (Admin Only)" style={{background:'#e74c3c',color:'#fff',border:'none',marginLeft:4}}>Delete</button>
 )}
 </td>
 </tr>
 )})}
 </tbody>
 </table>
 </div>
 </div>
 )}

 {/* ==================== CUSTOMER (WEB) ORDERS ==================== */}
 {activeModule === 'customerOrders' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Customer Orders (Web)</h2>
 </div>
 <div style={{display:'flex',gap:8,alignItems:'center'}}>
 <button className="btn btn-secondary" onClick={fetchCustomerOrders} disabled={customerOrdersLoading}>{customerOrdersLoading ? 'Refreshing…' : '↻ Refresh'}</button>
 </div>
 </div>
 <div style={{background:'#eef6ff',border:'1px solid #bcdcff',padding:'10px 14px',borderRadius:8,margin:'8px 0 14px',fontSize:13,color:'#1d4e89'}}>
 These are orders submitted by customers via the public order link. <b>Call the customer</b> to confirm the order, then click <b>Confirm &amp; Create Sales Order</b> to record it formally with stock allocation.
 </div>
 {customerOrdersLoading && (<div style={{padding:12}}>Loading customer orders…</div>)}
 {!customerOrdersLoading && customerOrders.length === 0 && (
 <div style={{padding:18,background:'#fafafa',border:'1px dashed #ccc',borderRadius:8,textAlign:'center',color:'#666'}}>No customer-submitted orders yet.</div>
 )}
 {!customerOrdersLoading && customerOrders.length > 0 && (
 <div style={{overflowX:'auto'}}>
 <table className="data-table" style={{minWidth:1100}}>
 <thead>
 <tr>
 <th>Order #</th>
 <th>Date</th>
 <th>Customer</th>
 <th>Phone</th>
 <th>Type</th>
 <th>Items</th>
 <th>Total (₦)</th>
 <th>Payment</th>
 <th>Evidence</th>
 <th>Status</th>
 <th>Actions</th>
 </tr>
 </thead>
 <tbody>
 {customerOrders.map(o => {
 const phoneClean = cleanPhoneForLink(o.phone);
 const isOpen = expandedCustomerOrder === o.id;
 const lines = customerOrderLines[o.id] || [];
 return (
 <React.Fragment key={o.id}>
 <tr>
 <td><b>{o.order_number}</b></td>
 <td>{o.order_date ? new Date(o.order_date).toLocaleString() : '—'}</td>
 <td>
 <div style={{fontWeight:600}}>{o.customer_name || '—'}</div>
 {o.address && <div style={{fontSize:11,color:'#666'}}>{o.address}</div>}
 </td>
 <td>
 {phoneClean ? (
 <div style={{display:'flex',gap:6,alignItems:'center'}}>
 <a href={`tel:${phoneClean}`} title="Call customer" style={{color:'#1565c0',fontWeight:600,textDecoration:'none'}}>📞 {o.phone}</a>
 <a href={`https://wa.me/${phoneClean.replace(/^\+/,'')}`} target="_blank" rel="noopener noreferrer" title="WhatsApp" style={{color:'#25d366',fontSize:16,textDecoration:'none'}}>💬</a>
 </div>
 ) : <span style={{color:'#999'}}>—</span>}
 </td>
 <td>
 <span style={{display:'inline-block',padding:'2px 8px',borderRadius:10,fontSize:11,fontWeight:600,background:o.customer_type==='wholesale'?'#fff3cd':'#d1ecf1',color:o.customer_type==='wholesale'?'#856404':'#0c5460'}}>
 {(o.customer_type||'retail').toUpperCase()}
 </span>
 </td>
 <td>
 <button className="btn btn-link" style={{padding:'2px 6px',fontSize:12}} onClick={() => {
 if (isOpen) { setExpandedCustomerOrder(null); }
 else { setExpandedCustomerOrder(o.id); if (!customerOrderLines[o.id]) fetchCustomerOrderLines(o.id); }
 }}>{o.line_count || lines.length || 0} item(s) {isOpen ? '▲' : '▼'}</button>
 </td>
 <td style={{textAlign:'right',fontWeight:600}}>{Number(o.total_amount||0).toLocaleString()}</td>
 <td>
 <span style={{display:'inline-block',padding:'2px 8px',borderRadius:10,fontSize:11,fontWeight:600,background:o.payment_status==='paid'?'#d4edda':'#f8d7da',color:o.payment_status==='paid'?'#155724':'#721c24'}}>
 {(o.payment_status||'unpaid').toUpperCase()}
 </span>
 </td>
 <td>
 {(o.evidence_files && o.evidence_files.length > 0) ? (
 <div style={{display:'flex',flexDirection:'column',gap:2}}>
 {o.evidence_files.map((f,i) => (
 <a key={i} href={`/api/public/admin/orders/${o.order_number}/evidence/${encodeURIComponent(f)}`} target="_blank" rel="noopener noreferrer" style={{fontSize:11,color:'#28a745',textDecoration:'underline'}}>📎 View receipt {i+1}</a>
 ))}
 </div>
 ) : <span style={{color:'#999',fontSize:12}}>—</span>}
 </td>
 <td>
 <span style={{display:'inline-block',padding:'2px 8px',borderRadius:10,fontSize:11,fontWeight:600,background:o.status==='confirmed'?'#d4edda':o.status==='cancelled'?'#f8d7da':'#fff3cd',color:o.status==='confirmed'?'#155724':o.status==='cancelled'?'#721c24':'#856404'}}>
 {(o.status||'pending').toUpperCase()}
 </span>
 </td>
 <td className="actions">
 <button className="btn-add" style={{whiteSpace:'nowrap'}} onClick={() => confirmCustomerOrderToSales(o)} title="Open the Sales Order form pre-filled with this customer's order">
 ✓ Confirm &amp; Create Sales Order
 </button>
 </td>
 </tr>
 {isOpen && (
 <tr>
 <td colSpan={11} style={{background:'#fafbff',padding:12}}>
 <div style={{fontWeight:600,marginBottom:6}}>Order lines</div>
 {lines.length === 0 ? (
 <div style={{color:'#666',fontSize:12}}>Loading lines…</div>
 ) : (
 <table style={{width:'100%',fontSize:12,borderCollapse:'collapse'}}>
 <thead>
 <tr style={{background:'#eef'}}>
 <th style={{textAlign:'left',padding:6}}>Product</th>
 <th style={{textAlign:'left',padding:6}}>SKU</th>
 <th style={{textAlign:'left',padding:6}}>Unit</th>
 <th style={{textAlign:'right',padding:6}}>Qty</th>
 <th style={{textAlign:'right',padding:6}}>Unit Price</th>
 <th style={{textAlign:'right',padding:6}}>Total</th>
 </tr>
 </thead>
 <tbody>
 {lines.map(l => (
 <tr key={l.id} style={{borderBottom:'1px solid #eee'}}>
 <td style={{padding:6}}>{l.product_name || '—'}</td>
 <td style={{padding:6}}>{l.product_sku || '—'}</td>
 <td style={{padding:6}}>{l.unit || '—'}</td>
 <td style={{padding:6,textAlign:'right'}}>{l.quantity}</td>
 <td style={{padding:6,textAlign:'right'}}>{Number(l.unit_price||0).toLocaleString()}</td>
 <td style={{padding:6,textAlign:'right'}}>{Number(l.line_total||0).toLocaleString()}</td>
 </tr>
 ))}
 </tbody>
 </table>
 )}
 {o.notes && (
 <div style={{marginTop:8,fontSize:11,color:'#555'}}>
 <b>Notes:</b> {o.notes}
 </div>
 )}
 </td>
 </tr>
 )}
 </React.Fragment>
 );
 })}
 </tbody>
 </table>
 </div>
 )}
 </div>
 )}

 {/* ==================== CUSTOMER MANAGEMENT ==================== */}
 {activeModule === 'customers' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Customer Management</h2>
 </div>
 <div style={{display:'flex',gap:8,alignItems:'center'}}>
 <input type="text" placeholder="Search customers..." value={custSearch} onChange={e=>setCustSearch(e.target.value)} style={{padding:'8px 12px',borderRadius:6,border:'1px solid #ddd',fontSize:13,width:220}} />
 <button onClick={() => { setCustEditing(null); setCustForm({ name:'', email:'', phone:'', address:'', credit_limit:'0' }); setCustShowForm(true); }} className="btn btn-primary">+ New Customer</button>
 </div>
 </div>

 {/* Summary Cards */}
 <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(180px,1fr))',gap:16,marginBottom:20}}>
 <div style={{background:'linear-gradient(135deg,#667eea,#764ba2)',borderRadius:12,padding:'18px 20px',color:'#fff'}}>
 <div style={{fontSize:12,opacity:0.8}}>Total Customers</div>
 <div style={{fontSize:28,fontWeight:700}}>{(data.customers||[]).length}</div>
 </div>
 <div style={{background:'linear-gradient(135deg,#2ecc71,#27ae60)',borderRadius:12,padding:'18px 20px',color:'#fff'}}>
 <div style={{fontSize:12,opacity:0.8}}>Active</div>
 <div style={{fontSize:28,fontWeight:700}}>{(data.customers||[]).filter(c=>c.is_active!==false).length}</div>
 </div>
 <div style={{background:'linear-gradient(135deg,#e74c3c,#c0392b)',borderRadius:12,padding:'18px 20px',color:'#fff'}}>
 <div style={{fontSize:12,opacity:0.8}}>Inactive</div>
 <div style={{fontSize:28,fontWeight:700}}>{(data.customers||[]).filter(c=>c.is_active===false).length}</div>
 </div>
 <div style={{background:'linear-gradient(135deg,#f39c12,#e67e22)',borderRadius:12,padding:'18px 20px',color:'#fff'}}>
 <div style={{fontSize:12,opacity:0.8}}>With Orders</div>
 <div style={{fontSize:28,fontWeight:700}}>{(data.customers||[]).filter(c=>(data.sales||[]).some(s=>s.customer_id===c.id)).length}</div>
 </div>
 </div>

 {/* Customer Registration / Edit Modal */}
 {custShowForm && (
 <div style={{position:'fixed',top:0,left:0,right:0,bottom:0,background:'rgba(0,0,0,0.5)',zIndex:10000,display:'flex',justifyContent:'center',alignItems:'center'}}>
 <div style={{background:'#fff',borderRadius:12,padding:30,width:'100%',maxWidth:500,boxShadow:'0 8px 32px rgba(0,0,0,0.2)'}}>
 <h3 style={{margin:'0 0 20px',color:'#2c3e50'}}>{custEditing ? 'Edit Customer' : 'Register New Customer'}</h3>
 <form onSubmit={async (e) => {
 e.preventDefault();
 try {
 const token = localStorage.getItem('access_token');
 const headers = { 'Content-Type':'application/json' };
 if (token) headers['Authorization'] = `Bearer ${token}`;
 const payload = { ...custForm, credit_limit: parseFloat(custForm.credit_limit)||0 };
 let res;
 if (custEditing) {
 res = await authedFetch(`/api/sales/customers/${custEditing.id}`, { method:'PUT', headers, body:JSON.stringify(payload) });
 } else {
 res = await authedFetch('/api/sales/customers', { method:'POST', headers, body:JSON.stringify(payload) });
 }
 if (!res.ok) { const err = await res.json(); throw new Error(err.detail||'Failed'); }
 notify(custEditing ? 'Customer updated successfully' : 'Customer registered successfully', 'success');
 setCustShowForm(false); setCustEditing(null);
 fetchData('customers');
 } catch(err) { notify(err.message, 'error'); }
 }}>
 <div style={{marginBottom:14}}>
 <label style={{display:'block',fontWeight:600,marginBottom:4,fontSize:13}}>Customer Name *</label>
 <input required type="text" value={custForm.name} onChange={e=>setCustForm(f=>({...f,name:e.target.value}))} style={{width:'100%',padding:'10px 12px',borderRadius:6,border:'1px solid #ddd',fontSize:14}} placeholder="Full name" />
 </div>
 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12,marginBottom:14}}>
 <div>
 <label style={{display:'block',fontWeight:600,marginBottom:4,fontSize:13}}>Email</label>
 <input type="email" value={custForm.email} onChange={e=>setCustForm(f=>({...f,email:e.target.value}))} style={{width:'100%',padding:'10px 12px',borderRadius:6,border:'1px solid #ddd',fontSize:14}} placeholder="email@example.com" />
 </div>
 <div>
 <label style={{display:'block',fontWeight:600,marginBottom:4,fontSize:13}}>Phone</label>
 <input type="text" value={custForm.phone} onChange={e=>setCustForm(f=>({...f,phone:e.target.value}))} style={{width:'100%',padding:'10px 12px',borderRadius:6,border:'1px solid #ddd',fontSize:14}} placeholder="+234..." />
 </div>
 </div>
 <div style={{marginBottom:14}}>
 <label style={{display:'block',fontWeight:600,marginBottom:4,fontSize:13}}>Address</label>
 <textarea value={custForm.address} onChange={e=>setCustForm(f=>({...f,address:e.target.value}))} style={{width:'100%',padding:'10px 12px',borderRadius:6,border:'1px solid #ddd',fontSize:14,minHeight:60}} placeholder="Customer address" />
 </div>
 <div style={{marginBottom:20}}>
 <label style={{display:'block',fontWeight:600,marginBottom:4,fontSize:13}}>Credit Limit (NGN)</label>
 <input type="number" min="0" step="0.01" value={custForm.credit_limit} onChange={e=>setCustForm(f=>({...f,credit_limit:e.target.value}))} style={{width:'100%',padding:'10px 12px',borderRadius:6,border:'1px solid #ddd',fontSize:14}} />
 </div>
 <div style={{display:'flex',gap:10,justifyContent:'flex-end'}}>
 <button type="button" onClick={()=>{setCustShowForm(false);setCustEditing(null);}} className="btn btn-secondary">Cancel</button>
 <button type="submit" className="btn btn-primary">{custEditing ? 'Update Customer' : 'Register Customer'}</button>
 </div>
 </form>
 </div>
 </div>
 )}

 {/* Customer Transaction View Modal */}
 {custViewOrders && (
 <div style={{position:'fixed',top:0,left:0,right:0,bottom:0,background:'rgba(0,0,0,0.5)',zIndex:10000,display:'flex',justifyContent:'center',alignItems:'flex-start',overflowY:'auto',padding:'30px 0'}}>
 <div style={{background:'#fff',borderRadius:12,padding:30,width:'100%',maxWidth:800,boxShadow:'0 8px 32px rgba(0,0,0,0.2)',margin:'auto'}}>
 <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:20}}>
 <div>
 <h3 style={{margin:0,color:'#2c3e50'}}>Transactions: {custViewOrders.name}</h3>
 <p style={{margin:'4px 0 0',fontSize:13,color:'#888'}}>{custViewOrders.customer_code} | {custViewOrders.phone||'No phone'} | {custViewOrders.email||'No email'}</p>
 </div>
 <button onClick={()=>{setCustViewOrders(null);setCustOrders([]);}} className="btn btn-secondary">Close</button>
 </div>
 {custLoadingOrders ? <p style={{textAlign:'center',color:'#888'}}>Loading transactions...</p> : custOrders.length === 0 ? (
 <div style={{textAlign:'center',padding:40,color:'#aaa'}}>
 <div style={{fontSize:48,marginBottom:10}}>No transactions found</div>
 <p>This customer has no sales orders yet.</p>
 </div>
 ) : (
 <div>
 <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(150px,1fr))',gap:12,marginBottom:16}}>
 <div style={{background:'#f0f4ff',borderRadius:8,padding:'12px 16px'}}>
 <div style={{fontSize:11,color:'#667eea',fontWeight:600}}>Total Orders</div>
 <div style={{fontSize:22,fontWeight:700,color:'#2c3e50'}}>{custOrders.length}</div>
 </div>
 <div style={{background:'#f0fff4',borderRadius:8,padding:'12px 16px'}}>
 <div style={{fontSize:11,color:'#2ecc71',fontWeight:600}}>Total Spent</div>
 <div style={{fontSize:22,fontWeight:700,color:'#2c3e50'}}>{formatCurrency(custOrders.reduce((s,o)=>s+(parseFloat(o.total_amount)||0),0))}</div>
 </div>
 <div style={{background:'#fff5f5',borderRadius:8,padding:'12px 16px'}}>
 <div style={{fontSize:11,color:'#e74c3c',fontWeight:600}}>Unpaid</div>
 <div style={{fontSize:22,fontWeight:700,color:'#e74c3c'}}>{custOrders.filter(o=>o.payment_status==='unpaid').length}</div>
 </div>
 <div style={{background:'#fffaf0',borderRadius:8,padding:'12px 16px'}}>
 <div style={{fontSize:11,color:'#f39c12',fontWeight:600}}>Partial</div>
 <div style={{fontSize:22,fontWeight:700,color:'#f39c12'}}>{custOrders.filter(o=>o.payment_status==='partial').length}</div>
 </div>
 </div>
 <div style={{maxHeight:400,overflowY:'auto'}}>
 <table className="data-table">
 <thead><tr><th>Order #</th><th>Date</th><th>Amount</th><th>Status</th><th>Payment</th></tr></thead>
 <tbody>
 {custOrders.map(o => (
 <tr key={o.id}>
 <td style={{fontWeight:600}}>{o.order_number}</td>
 <td>{o.order_date ? new Date(o.order_date).toLocaleDateString() : o.created_at ? new Date(o.created_at).toLocaleDateString() : '-'}</td>
 <td style={{fontWeight:600}}>{formatCurrency(o.total_amount)}</td>
 <td><span className={`status ${o.status}`}>{o.status}</span></td>
 <td><span className={`status ${o.payment_status||'unpaid'}`}>{o.payment_status||'unpaid'}</span></td>
 </tr>
 ))}
 </tbody>
 </table>
 </div>
 </div>
 )}
 </div>
 </div>
 )}

 {/* Customers Table */}
 <div className="table-container">
 <table className="data-table">
 <thead>
 <tr>
 <th>Code</th>
 <th>Name</th>
 <th>Phone</th>
 <th>Email</th>
 <th>Address</th>
 <th>Credit Limit</th>
 <th>Status</th>
 <th>Orders</th>
 <th>Actions</th>
 </tr>
 </thead>
 <tbody>
 {(data.customers||[]).filter(c => {
 if (!custSearch) return true;
 const s = custSearch.toLowerCase();
 return (c.name||'').toLowerCase().includes(s) || (c.phone||'').toLowerCase().includes(s) || (c.email||'').toLowerCase().includes(s) || (c.customer_code||'').toLowerCase().includes(s) || (c.address||'').toLowerCase().includes(s);
 }).map(c => {
 const orderCount = (data.sales||[]).filter(o=>o.customer_id===c.id).length;
 return (
 <tr key={c.id} style={{opacity: c.is_active===false ? 0.5 : 1}}>
 <td style={{fontWeight:600,color:'#667eea'}}>{c.customer_code||'-'}</td>
 <td style={{fontWeight:600}}>{c.name}</td>
 <td>{c.phone||'-'}</td>
 <td>{c.email||'-'}</td>
 <td style={{maxWidth:200,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}} title={c.address||''}>{c.address||'-'}</td>
 <td>{formatCurrency(c.credit_limit||0)}</td>
 <td><span className={`status ${c.is_active!==false?'confirmed':'cancelled'}`}>{c.is_active!==false?'Active':'Inactive'}</span></td>
 <td style={{fontWeight:600}}>{orderCount}</td>
 <td className="actions" style={{whiteSpace:'nowrap'}}>
 <button onClick={async () => {
 setCustViewOrders(c); setCustLoadingOrders(true);
 try {
 const token = localStorage.getItem('access_token');
 const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
 const res = await authedFetch(`/api/sales/orders?customer_id=${c.id}&limit=100`, { headers });
 const d = await res.json();
 setCustOrders(d.items||d||[]);
 } catch(err) { notify('Error loading orders','error'); setCustOrders([]); }
 finally { setCustLoadingOrders(false); }
 }} className="btn btn-primary" style={{fontSize:11,padding:'4px 10px',marginRight:4}} title="View Transactions">Transactions</button>
 <button onClick={() => {
 setCustEditing(c);
 setCustForm({ name:c.name||'', email:c.email||'', phone:c.phone||'', address:c.address||'', credit_limit:String(c.credit_limit||0) });
 setCustShowForm(true);
 }} className="btn btn-secondary" style={{fontSize:11,padding:'4px 10px',marginRight:4}} title="Edit Customer">Edit</button>
 {c.is_active!==false ? (
 <button onClick={async () => {
 if (!window.confirm(`Deactivate customer "${c.name}"?`)) return;
 try {
 const token = localStorage.getItem('access_token');
 const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
 const res = await authedFetch(`/api/sales/customers/${c.id}`, { method:'DELETE', headers });
 if (!res.ok) throw new Error('Failed');
 notify('Customer deactivated','success'); fetchData('customers');
 } catch(err) { notify(err.message,'error'); }
 }} className="btn btn-danger" style={{fontSize:11,padding:'4px 10px',background:'#e74c3c',color:'#fff',border:'none'}} title="Deactivate Customer">Deactivate</button>
 ) : (
 <button onClick={async () => {
 try {
 const token = localStorage.getItem('access_token');
 const headers = { 'Content-Type':'application/json' };
 if (token) headers['Authorization'] = `Bearer ${token}`;
 const res = await authedFetch(`/api/sales/customers/${c.id}`, { method:'PUT', headers, body:JSON.stringify({is_active:true}) });
 if (!res.ok) throw new Error('Failed');
 notify('Customer reactivated','success'); fetchData('customers');
 } catch(err) { notify(err.message,'error'); }
 }} className="btn btn-success" style={{fontSize:11,padding:'4px 10px'}} title="Reactivate Customer">Reactivate</button>
 )}
 </td>
 </tr>
 );
 })}
 </tbody>
 </table>
 {(data.customers||[]).length === 0 && <p style={{textAlign:'center',padding:30,color:'#aaa'}}>No customers yet. Click "+ New Customer" to register one.</p>}
 </div>
 </div>
 )}

 {/* Warehouse Transfers */}
 {activeModule === 'transfers' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Warehouse Transfers</h2>
 </div>
 <button onClick={() => { setShowTransferForm(true); setTransferForm({ from_warehouse_id: '', to_warehouse_id: '', product_id: '', quantity: '', reason: '', notes: '' }); }} className="btn btn-primary">New Transfer</button>
 </div>

 {/* Summary Cards */}
 <div className="stats-cards" style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(200px,1fr))',gap:16,marginBottom:24}}>
 <div className="stat-card" style={{background:'linear-gradient(135deg,#667eea 0%,#764ba2 100%)',color:'#fff',borderRadius:12,padding:20}}>
 <div style={{fontSize:28,fontWeight:700}}>{transferSummary.total_transfers}</div>
 <div style={{fontSize:13,opacity:0.85}}>Total Transfers</div>
 </div>
 <div className="stat-card" style={{background:'linear-gradient(135deg,#43e97b 0%,#38f9d7 100%)',color:'#fff',borderRadius:12,padding:20}}>
 <div style={{fontSize:28,fontWeight:700}}>{transferSummary.total_quantity_moved.toLocaleString()}</div>
 <div style={{fontSize:13,opacity:0.85}}>Total Units Moved</div>
 </div>
 <div className="stat-card" style={{background:'linear-gradient(135deg,#f093fb 0%,#f5576c 100%)',color:'#fff',borderRadius:12,padding:20}}>
 <div style={{fontSize:28,fontWeight:700}}>{transferSummary.today_transfers}</div>
 <div style={{fontSize:13,opacity:0.85}}>Today's Transfers</div>
 </div>
 </div>

 {/* Transfer Form Modal */}
 {showTransferForm && (
 <div className="modal-overlay">
 <div className="modal" style={{maxWidth:600}}>
 <div className="modal-header">
 <div className="modal-header-left"><img src="/company-logo.png" alt="AstroBSM" className="modal-logo" onError={(e) => { e.target.style.display = 'none'; }}/><h3>New Warehouse Transfer</h3></div>
 <button className="modal-close" onClick={() => setShowTransferForm(false)}>x</button>
 </div>
 <div className="modal-content">
 <form onSubmit={async (e) => {
 e.preventDefault();
 setTransferLoading(true);
 try {
 const token = localStorage.getItem('access_token');
 const headers = { 'Content-Type': 'application/json' };
 if (token) headers['Authorization'] = `Bearer ${token}`;
 const res = await authedFetch('/api/transfers/', {
 method: 'POST', headers,
 body: JSON.stringify({
 from_warehouse_id: transferForm.from_warehouse_id,
 to_warehouse_id: transferForm.to_warehouse_id,
 product_id: transferForm.product_id,
 quantity: parseFloat(transferForm.quantity),
 reason: transferForm.reason,
 notes: transferForm.notes || null
 })
 });
 const d = await res.json();
 if (!res.ok) throw new Error(d.detail || 'Transfer failed');
 notify(d.message, 'success');
 setShowTransferForm(false);
 fetchTransfers();
 fetchTransferSummary();
 } catch (err) { notify(err.message, 'error'); }
 finally { setTransferLoading(false); }
 }}>
 <div className="form-row">
 <div className="form-group">
 <label>Source Warehouse *</label>
 <select value={transferForm.from_warehouse_id} onChange={(e) => setTransferForm(f => ({...f, from_warehouse_id: e.target.value}))} required>
 <option value="">Select source warehouse</option>
 {(data.warehouses||[]).map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
 </select>
 </div>
 <div className="form-group">
 <label>Destination Warehouse *</label>
 <select value={transferForm.to_warehouse_id} onChange={(e) => setTransferForm(f => ({...f, to_warehouse_id: e.target.value}))} required>
 <option value="">Select destination warehouse</option>
 {(data.warehouses||[]).filter(w => w.id !== transferForm.from_warehouse_id).map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
 </select>
 </div>
 </div>
 <div className="form-row">
 <div className="form-group">
 <label>Product *</label>
 <select value={transferForm.product_id} onChange={(e) => setTransferForm(f => ({...f, product_id: e.target.value}))} required>
 <option value="">Select product</option>
 {(data.products||[]).map(p => <option key={p.id} value={p.id}>{p.name} ({p.sku})</option>)}
 </select>
 </div>
 <div className="form-group">
 <label>Quantity *</label>
 <input type="number" step="0.01" min="0.01" value={transferForm.quantity} onChange={(e) => setTransferForm(f => ({...f, quantity: e.target.value}))} required placeholder="Enter quantity"/>
 </div>
 </div>
 <div className="form-group">
 <label>Reason for Transfer *</label>
 <select value={transferForm.reason} onChange={(e) => setTransferForm(f => ({...f, reason: e.target.value}))} required>
 <option value="">Select reason</option>
 <option value="Restock Sales Warehouse">Restock Sales Warehouse</option>
 <option value="Production Surplus">Production Surplus</option>
 <option value="Customer Demand">Customer Demand</option>
 <option value="Warehouse Consolidation">Warehouse Consolidation</option>
 <option value="Stock Rebalancing">Stock Rebalancing</option>
 <option value="Quality Control Move">Quality Control Move</option>
 <option value="Other">Other (specify in notes)</option>
 </select>
 </div>
 <div className="form-group">
 <label>Notes</label>
 <textarea rows="2" value={transferForm.notes} onChange={(e) => setTransferForm(f => ({...f, notes: e.target.value}))} placeholder="Optional additional details..."/>
 </div>
 <div className="modal-actions">
 <button type="button" className="btn btn-secondary" onClick={() => setShowTransferForm(false)}>Cancel</button>
 <button type="submit" className="btn btn-primary" disabled={transferLoading}>{transferLoading ? 'Processing...' : 'Complete Transfer'}</button>
 </div>
 </form>
 </div>
 </div>
 </div>
 )}

 {/* Transfers Table */}
 <div className="table-container">
 <table className="data-table">
 <thead>
 <tr>
 <th>Transfer #</th>
 <th>From</th>
 <th>To</th>
 <th>Product</th>
 <th>Qty</th>
 <th>Reason</th>
 <th>By</th>
 <th>Date</th>
 <th>Status</th>
 </tr>
 </thead>
 <tbody>
 {transfersList.map(t => (
 <tr key={t.id}>
 <td style={{fontWeight:600,color:'#667eea'}}>{t.transfer_number}</td>
 <td>{t.from_warehouse_name}</td>
 <td>{t.to_warehouse_name}</td>
 <td>{t.product_name} <span style={{fontSize:10,color:'#999'}}>({t.product_sku})</span></td>
 <td style={{fontWeight:600}}>{t.quantity.toLocaleString()}</td>
 <td><span style={{background:'#e8f5e9',color:'#2e7d32',padding:'2px 8px',borderRadius:12,fontSize:11}}>{t.reason}</span></td>
 <td>{t.created_by_name || 'System'}</td>
 <td>{t.created_at ? new Date(t.created_at).toLocaleDateString() : 'N/A'}</td>
 <td><span className={`status ${t.status}`}>{t.status}</span></td>
 </tr>
 ))}
 {transfersList.length === 0 && (
 <tr><td colSpan="9" style={{textAlign:'center',padding:30,color:'#aaa'}}>No transfers yet. Click "New Transfer" to move products between warehouses.</td></tr>
 )}
 </tbody>
 </table>
 </div>
 </div>
 )}

 {/* ============ RETURNED PRODUCTS MODULE ============ */}
 {activeModule === 'returns' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Returned Products</h2>
 </div>
 <button onClick={() => { setShowReturnForm(true); setEditingReturn(null); setReturnForm({ warehouse_id: '', product_id: '', sales_order_id: '', customer_id: '', quantity: '', return_reason: '', return_condition: 'good', refund_amount: '', notes: '', price_type: 'retail' }); }} className="btn btn-primary">Record Return</button>
 </div>

 {/* Summary Cards */}
 <div className="stats-cards" style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(180px,1fr))',gap:16,marginBottom:24}}>
 <div className="stat-card" style={{background:'linear-gradient(135deg,#f093fb 0%,#f5576c 100%)',color:'#fff',borderRadius:12,padding:20}}>
 <div style={{fontSize:28,fontWeight:700}}>{returnsSummary.total_returns}</div>
 <div style={{fontSize:13,opacity:0.85}}>Total Returns</div>
 </div>
 <div className="stat-card" style={{background:'linear-gradient(135deg,#667eea 0%,#764ba2 100%)',color:'#fff',borderRadius:12,padding:20}}>
 <div style={{fontSize:28,fontWeight:700}}>{returnsSummary.total_quantity_returned.toLocaleString()}</div>
 <div style={{fontSize:13,opacity:0.85}}>Total Qty Returned</div>
 </div>
 <div className="stat-card" style={{background:'linear-gradient(135deg,#fa709a 0%,#fee140 100%)',color:'#fff',borderRadius:12,padding:20}}>
 <div style={{fontSize:28,fontWeight:700}}>{returnsSummary.pending_refunds}</div>
 <div style={{fontSize:13,opacity:0.85}}>Pending Refunds</div>
 </div>
 <div className="stat-card" style={{background:'linear-gradient(135deg,#43e97b 0%,#38f9d7 100%)',color:'#fff',borderRadius:12,padding:20}}>
 <div style={{fontSize:28,fontWeight:700}}>{returnsSummary.today_returns}</div>
 <div style={{fontSize:13,opacity:0.85}}>Today's Returns</div>
 </div>
 <div className="stat-card" style={{background:'linear-gradient(135deg,#4facfe 0%,#00f2fe 100%)',color:'#fff',borderRadius:12,padding:20}}>
 <div style={{fontSize:28,fontWeight:700}}>{Number(returnsSummary.total_refund_amount).toLocaleString('en-NG', {style:'currency',currency:'NGN'})}</div>
 <div style={{fontSize:13,opacity:0.85}}>Total Refunded</div>
 </div>
 </div>

 {/* Condition Breakdown */}
 {returnsSummary.condition_breakdown && Object.keys(returnsSummary.condition_breakdown).length > 0 && (
 <div style={{display:'flex',gap:12,marginBottom:20,flexWrap:'wrap'}}>
 {Object.entries(returnsSummary.condition_breakdown).map(([cond, count]) => (
 <span key={cond} style={{background: cond === 'good' ? '#e8f5e9' : cond === 'damaged' ? '#fce4ec' : cond === 'defective' ? '#fff3e0' : '#f3e5f5', color: cond === 'good' ? '#2e7d32' : cond === 'damaged' ? '#c62828' : cond === 'defective' ? '#e65100' : '#6a1b9a', padding:'6px 14px',borderRadius:20,fontSize:12,fontWeight:600}}>
 {cond.charAt(0).toUpperCase() + cond.slice(1)}: {count}
 </span>
 ))}
 </div>
 )}

 {/* Return Form Modal */}
 {showReturnForm && (
 <div className="modal-overlay">
 <div className="modal" style={{maxWidth:650}}>
 <div className="modal-header">
 <div className="modal-header-left"><img src="/company-logo.png" alt="AstroBSM" className="modal-logo" onError={(e) => { e.target.style.display = 'none'; }}/><h3>{editingReturn ? 'Update Return' : 'Record Product Return'}</h3></div>
 <button className="modal-close" onClick={() => setShowReturnForm(false)}>x</button>
 </div>
 <div className="modal-content">
 <form onSubmit={async (e) => {
 e.preventDefault();
 setReturnLoading(true);
 try {
 const token = localStorage.getItem('access_token');
 const headers = { 'Content-Type': 'application/json' };
 if (token) headers['Authorization'] = `Bearer ${token}`;

 if (editingReturn) {
 // Update existing return
 const res = await authedFetch(`/api/returns/${editingReturn.id}`, {
 method: 'PUT', headers,
 body: JSON.stringify({
 refund_status: returnForm.refund_status || undefined,
 refund_amount: returnForm.refund_amount ? parseFloat(returnForm.refund_amount) : undefined,
 notes: returnForm.notes || undefined,
 return_condition: returnForm.return_condition || undefined,
 })
 });
 const d = await res.json();
 if (!res.ok) throw new Error(d.detail || 'Update failed');
 notify(d.message || 'Return updated', 'success');
 } else {
 // Create new return
 const res = await authedFetch('/api/returns/', {
 method: 'POST', headers,
 body: JSON.stringify({
 warehouse_id: returnForm.warehouse_id,
 product_id: returnForm.product_id,
 sales_order_id: returnForm.sales_order_id || null,
 customer_id: returnForm.customer_id || null,
 quantity: parseFloat(returnForm.quantity),
 return_reason: returnForm.return_reason,
 return_condition: returnForm.return_condition,
 refund_amount: returnForm.refund_amount ? parseFloat(returnForm.refund_amount) : null,
 notes: returnForm.notes || null
 })
 });
 const d = await res.json();
 if (!res.ok) throw new Error(d.detail || 'Failed to record return');
 notify(d.message, 'success');
 }
 setShowReturnForm(false);
 setEditingReturn(null);
 fetchReturns();
 fetchReturnsSummary();
 } catch (err) { notify(err.message, 'error'); }
 finally { setReturnLoading(false); }
 }}>
 {!editingReturn && (<>
 <div className="form-row">
 <div className="form-group">
 <label>Warehouse *</label>
 <select value={returnForm.warehouse_id} onChange={(e) => setReturnForm(f => ({...f, warehouse_id: e.target.value}))} required>
 <option value="">Select warehouse</option>
 {(data.warehouses||[]).map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
 </select>
 </div>
 <div className="form-group">
 <label>Product *</label>
 <select value={returnForm.product_id} onChange={(e) => {
 const pid = e.target.value;
 const prod = (data.products||[]).find(p=>p.id===pid);
 const priceType = returnForm.price_type || 'retail';
 const pr = prod?.pricing?.[0] || {};
 const unitPrice = priceType === 'wholesale'
 ? (parseFloat(pr.wholesale_price)||parseFloat(prod?.wholesale_price)||parseFloat(prod?.selling_price)||0)
 : (parseFloat(pr.retail_price)||parseFloat(prod?.retail_price)||parseFloat(prod?.selling_price)||0);
 const qty = parseFloat(returnForm.quantity) || 0;
 setReturnForm(f => ({...f, product_id: pid, refund_amount: (unitPrice * qty).toFixed(2)}));
 }} required>
 <option value="">Select product</option>
 {(data.products||[]).map(p => <option key={p.id} value={p.id}>{p.name} ({p.sku})</option>)}
 </select>
 </div>
 </div>
 <div className="form-row">
 <div className="form-group">
 <label>Customer</label>
 <select value={returnForm.customer_id} onChange={(e) => setReturnForm(f => ({...f, customer_id: e.target.value}))}>
 <option value="">Select customer (optional)</option>
 {(data.customers||[]).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
 </select>
 </div>
 <div className="form-group">
 <label>Quantity *</label>
 <input type="number" step="0.01" min="0.01" value={returnForm.quantity} onChange={(e) => {
 const qty = parseFloat(e.target.value) || 0;
 const prod = (data.products||[]).find(p=>p.id===returnForm.product_id);
 const priceType = returnForm.price_type || 'retail';
 const pr = prod?.pricing?.[0] || {};
 const unitPrice = priceType === 'wholesale'
 ? (parseFloat(pr.wholesale_price)||parseFloat(prod?.wholesale_price)||parseFloat(prod?.selling_price)||0)
 : (parseFloat(pr.retail_price)||parseFloat(prod?.retail_price)||parseFloat(prod?.selling_price)||0);
 setReturnForm(f => ({...f, quantity: e.target.value, refund_amount: (unitPrice * qty).toFixed(2)}));
 }} required placeholder="Enter quantity"/>
 </div>
 </div>
 <div className="form-row">
 <div className="form-group">
 <label>Price Type *</label>
 <select value={returnForm.price_type || 'retail'} onChange={(e) => {
 const priceType = e.target.value;
 const prod = (data.products||[]).find(p=>p.id===returnForm.product_id);
 const pr = prod?.pricing?.[0] || {};
 const unitPrice = priceType === 'wholesale'
 ? (parseFloat(pr.wholesale_price)||parseFloat(prod?.wholesale_price)||parseFloat(prod?.selling_price)||0)
 : (parseFloat(pr.retail_price)||parseFloat(prod?.retail_price)||parseFloat(prod?.selling_price)||0);
 const qty = parseFloat(returnForm.quantity) || 0;
 setReturnForm(f => ({...f, price_type: priceType, refund_amount: (unitPrice * qty).toFixed(2)}));
 }}>
 <option value="retail">Retail Price</option>
 <option value="wholesale">Wholesale Price</option>
 </select>
 </div>
 <div className="form-group">
 <label>Unit Price</label>
 <input type="text" readOnly value={(() => {
 const prod = (data.products||[]).find(p=>p.id===returnForm.product_id);
 if (!prod) return '---';
 const pr = prod?.pricing?.[0] || {};
 const priceType = returnForm.price_type || 'retail';
 const price = priceType === 'wholesale'
 ? (parseFloat(pr.wholesale_price)||parseFloat(prod?.wholesale_price)||parseFloat(prod?.selling_price)||0)
 : (parseFloat(pr.retail_price)||parseFloat(prod?.retail_price)||parseFloat(prod?.selling_price)||0);
 return `\u20A6${price.toLocaleString()}`;
 })()} style={{background:'#f5f5f5',fontWeight:600}}/>
 </div>
 </div>
 </>)}
 <div className="form-row">
 <div className="form-group">
 <label>Return Condition *</label>
 <select value={returnForm.return_condition} onChange={(e) => setReturnForm(f => ({...f, return_condition: e.target.value}))} required>
 <option value="good">Good (Restockable)</option>
 <option value="damaged">Damaged</option>
 <option value="defective">Defective</option>
 <option value="expired">Expired</option>
 </select>
 </div>
 <div className="form-group">
 <label>Refund Amount (auto-calculated)</label>
 <input type="number" step="0.01" min="0" value={returnForm.refund_amount} onChange={(e) => setReturnForm(f => ({...f, refund_amount: e.target.value}))} placeholder="0.00" style={{fontWeight:600,fontSize:15}}/>
 </div>
 </div>
 {!editingReturn && (
 <div className="form-group">
 <label>Reason for Return *</label>
 <select value={returnForm.return_reason} onChange={(e) => setReturnForm(f => ({...f, return_reason: e.target.value}))} required>
 <option value="">Select reason</option>
 <option value="Product Defect">Product Defect</option>
 <option value="Wrong Product Delivered">Wrong Product Delivered</option>
 <option value="Customer Changed Mind">Customer Changed Mind</option>
 <option value="Product Expired">Product Expired</option>
 <option value="Damaged During Delivery">Damaged During Delivery</option>
 <option value="Quality Below Expectation">Quality Below Expectation</option>
 <option value="Excess Stock Return">Excess Stock Return</option>
 <option value="Recalled Product">Recalled Product</option>
 <option value="Other">Other (specify in notes)</option>
 </select>
 </div>
 )}
 {editingReturn && (
 <div className="form-group">
 <label>Refund Status</label>
 <select value={returnForm.refund_status || ''} onChange={(e) => setReturnForm(f => ({...f, refund_status: e.target.value}))}>
 <option value="pending">Pending</option>
 <option value="approved">Approved</option>
 <option value="processed">Processed</option>
 <option value="rejected">Rejected</option>
 </select>
 </div>
 )}
 <div className="form-group">
 <label>Notes</label>
 <textarea rows="2" value={returnForm.notes} onChange={(e) => setReturnForm(f => ({...f, notes: e.target.value}))} placeholder="Additional details, action taken..."/>
 </div>
 <div className="modal-actions">
 <button type="button" className="btn btn-secondary" onClick={() => { setShowReturnForm(false); setEditingReturn(null); }}>Cancel</button>
 <button type="submit" className="btn btn-primary" disabled={returnLoading}>{returnLoading ? 'Processing...' : editingReturn ? 'Update Return' : 'Record Return'}</button>
 </div>
 </form>
 </div>
 </div>
 </div>
 )}

 {/* Returns Table */}
 <div className="table-container">
 <table className="data-table">
 <thead>
 <tr>
 <th>Date</th>
 <th>Product</th>
 <th>Warehouse</th>
 <th>Customer</th>
 <th>Qty</th>
 <th>Reason</th>
 <th>Condition</th>
 <th>Refund Status</th>
 <th>Refund Amt</th>
 <th>Processed By</th>
 <th>Actions</th>
 </tr>
 </thead>
 <tbody>
 {returnsList.map(r => (
 <tr key={r.id}>
 <td>{r.return_date ? new Date(r.return_date).toLocaleDateString() : 'N/A'}</td>
 <td>{r.product_name} <span style={{fontSize:10,color:'#999'}}>({r.product_sku})</span></td>
 <td>{r.warehouse_name}</td>
 <td>{r.customer_name || '-'}</td>
 <td style={{fontWeight:600}}>{r.quantity.toLocaleString()}</td>
 <td><span style={{background:'#fff3e0',color:'#e65100',padding:'2px 8px',borderRadius:12,fontSize:11}}>{r.return_reason}</span></td>
 <td><span style={{background: r.return_condition === 'good' ? '#e8f5e9' : '#fce4ec', color: r.return_condition === 'good' ? '#2e7d32' : '#c62828', padding:'2px 8px',borderRadius:12,fontSize:11,fontWeight:600}}>{r.return_condition}</span></td>
 <td><span className={`status ${r.refund_status}`} style={{fontSize:11}}>{r.refund_status}</span></td>
 <td>{r.refund_amount ? Number(r.refund_amount).toLocaleString('en-NG', {style:'currency',currency:'NGN'}) : '-'}</td>
 <td>{r.processed_by_name || '-'}</td>
 <td>
 <button className="btn btn-sm" style={{fontSize:10,padding:'3px 8px',marginRight:4}} onClick={() => {
 setEditingReturn(r);
 setReturnForm({ ...returnForm, return_condition: r.return_condition, refund_status: r.refund_status, refund_amount: r.refund_amount || '', notes: r.notes || '' });
 setShowReturnForm(true);
 }}>Edit</button>
 <button className="btn btn-sm" style={{fontSize:10,padding:'3px 8px',background:'#ef5350',color:'#fff'}} onClick={async () => {
 if (!window.confirm('Delete this return record?')) return;
 try {
 const token = localStorage.getItem('access_token');
 const headers = {};
 if (token) headers['Authorization'] = `Bearer ${token}`;
 const res = await authedFetch(`/api/returns/${r.id}`, { method: 'DELETE', headers });
 if (!res.ok) throw new Error('Delete failed');
 notify('Return deleted', 'success');
 fetchReturns(); fetchReturnsSummary();
 } catch(err) { notify(err.message, 'error'); }
 }}>Del</button>
 </td>
 </tr>
 ))}
 {returnsList.length === 0 && (
 <tr><td colSpan="11" style={{textAlign:'center',padding:30,color:'#aaa'}}>No returns recorded yet. Click "Record Return" to log a product return.</td></tr>
 )}
 </tbody>
 </table>
 </div>
 </div>
 )}

 {/* ============ DAMAGED PRODUCT TRANSFERS MODULE ============ */}
 {activeModule === 'damagedTransfers' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Damaged Product Transfers</h2>
 </div>
 <button onClick={() => { setShowDamagedTransferForm(true); setDamagedTransferForm({ from_warehouse_id: '', to_warehouse_id: '', product_id: '', raw_material_id: '', quantity: '', damage_type: '', damage_reason: '', action_taken: '', notes: '' }); }} className="btn btn-primary">New Damaged Transfer</button>
 </div>

 {/* Summary Cards */}
 <div className="stats-cards" style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(180px,1fr))',gap:16,marginBottom:24}}>
 <div className="stat-card" style={{background:'linear-gradient(135deg,#f093fb 0%,#f5576c 100%)',color:'#fff',borderRadius:12,padding:20}}>
 <div style={{fontSize:28,fontWeight:700}}>{damagedTransfersSummary.total_transfers}</div>
 <div style={{fontSize:13,opacity:0.85}}>Total Damaged Transfers</div>
 </div>
 <div className="stat-card" style={{background:'linear-gradient(135deg,#fa709a 0%,#fee140 100%)',color:'#fff',borderRadius:12,padding:20}}>
 <div style={{fontSize:28,fontWeight:700}}>{damagedTransfersSummary.pending}</div>
 <div style={{fontSize:13,opacity:0.85}}>Pending</div>
 </div>
 <div className="stat-card" style={{background:'linear-gradient(135deg,#667eea 0%,#764ba2 100%)',color:'#fff',borderRadius:12,padding:20}}>
 <div style={{fontSize:28,fontWeight:700}}>{damagedTransfersSummary.dispatched}</div>
 <div style={{fontSize:13,opacity:0.85}}>Dispatched</div>
 </div>
 <div className="stat-card" style={{background:'linear-gradient(135deg,#43e97b 0%,#38f9d7 100%)',color:'#fff',borderRadius:12,padding:20}}>
 <div style={{fontSize:28,fontWeight:700}}>{damagedTransfersSummary.received}</div>
 <div style={{fontSize:13,opacity:0.85}}>Received</div>
 </div>
 <div className="stat-card" style={{background:'linear-gradient(135deg,#4facfe 0%,#00f2fe 100%)',color:'#fff',borderRadius:12,padding:20}}>
 <div style={{fontSize:28,fontWeight:700}}>{damagedTransfersSummary.total_quantity.toLocaleString()}</div>
 <div style={{fontSize:13,opacity:0.85}}>Total Units</div>
 </div>
 </div>

 {/* Damaged Transfer Form Modal */}
 {showDamagedTransferForm && (
 <div className="modal-overlay">
 <div className="modal" style={{maxWidth:650}}>
 <div className="modal-header">
 <div className="modal-header-left"><img src="/company-logo.png" alt="AstroBSM" className="modal-logo" onError={(e) => { e.target.style.display = 'none'; }}/><h3>Transfer Damaged Product</h3></div>
 <button className="modal-close" onClick={() => setShowDamagedTransferForm(false)}>x</button>
 </div>
 <div className="modal-content">
 <form onSubmit={async (e) => {
 e.preventDefault();
 setDamagedTransferLoading(true);
 try {
 const token = localStorage.getItem('access_token');
 const headers = { 'Content-Type': 'application/json' };
 if (token) headers['Authorization'] = `Bearer ${token}`;
 const res = await authedFetch('/api/damaged-transfers/', {
 method: 'POST', headers,
 body: JSON.stringify({
 from_warehouse_id: damagedTransferForm.from_warehouse_id,
 to_warehouse_id: damagedTransferForm.to_warehouse_id,
 product_id: damagedTransferForm.product_id || null,
 raw_material_id: damagedTransferForm.raw_material_id || null,
 quantity: parseFloat(damagedTransferForm.quantity),
 damage_type: damagedTransferForm.damage_type,
 damage_reason: damagedTransferForm.damage_reason,
 action_taken: damagedTransferForm.action_taken,
 notes: damagedTransferForm.notes || null
 })
 });
 const d = await res.json();
 if (!res.ok) throw new Error(d.detail || 'Transfer failed');
 notify(d.message, 'success');
 setShowDamagedTransferForm(false);
 fetchDamagedTransfers();
 fetchDamagedTransfersSummary();
 } catch (err) { notify(err.message, 'error'); }
 finally { setDamagedTransferLoading(false); }
 }}>
 <div className="form-row">
 <div className="form-group">
 <label>Source Warehouse *</label>
 <select value={damagedTransferForm.from_warehouse_id} onChange={(e) => setDamagedTransferForm(f => ({...f, from_warehouse_id: e.target.value}))} required>
 <option value="">Select source warehouse</option>
 {(data.warehouses||[]).map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
 </select>
 </div>
 <div className="form-group">
 <label>Destination Warehouse *</label>
 <select value={damagedTransferForm.to_warehouse_id} onChange={(e) => setDamagedTransferForm(f => ({...f, to_warehouse_id: e.target.value}))} required>
 <option value="">Select destination</option>
 {(data.warehouses||[]).filter(w => w.id !== damagedTransferForm.from_warehouse_id).map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
 </select>
 </div>
 </div>
 <div className="form-row">
 <div className="form-group">
 <label>Product</label>
 <select value={damagedTransferForm.product_id} onChange={(e) => setDamagedTransferForm(f => ({...f, product_id: e.target.value, raw_material_id: ''}))}>
 <option value="">Select product (or raw material below)</option>
 {(data.products||[]).map(p => <option key={p.id} value={p.id}>{p.name} ({p.sku})</option>)}
 </select>
 </div>
 <div className="form-group">
 <label>Raw Material</label>
 <select value={damagedTransferForm.raw_material_id} onChange={(e) => setDamagedTransferForm(f => ({...f, raw_material_id: e.target.value, product_id: ''}))}>
 <option value="">Select raw material (or product above)</option>
 {(data.rawMaterials||[]).map(rm => <option key={rm.id} value={rm.id}>{rm.name} ({rm.sku})</option>)}
 </select>
 </div>
 </div>
 <div className="form-row">
 <div className="form-group">
 <label>Quantity *</label>
 <input type="number" step="0.01" min="0.01" value={damagedTransferForm.quantity} onChange={(e) => setDamagedTransferForm(f => ({...f, quantity: e.target.value}))} required placeholder="Enter quantity"/>
 </div>
 <div className="form-group">
 <label>Damage Type *</label>
 <select value={damagedTransferForm.damage_type} onChange={(e) => setDamagedTransferForm(f => ({...f, damage_type: e.target.value}))} required>
 <option value="">Select damage type</option>
 <option value="Physical Damage">Physical Damage</option>
 <option value="Water Damage">Water Damage</option>
 <option value="Expired">Expired</option>
 <option value="Manufacturing Defect">Manufacturing Defect</option>
 <option value="Contamination">Contamination</option>
 <option value="Packaging Damage">Packaging Damage</option>
 <option value="Heat Damage">Heat Damage</option>
 <option value="Pest Damage">Pest Damage</option>
 <option value="Other">Other</option>
 </select>
 </div>
 </div>
 <div className="form-group">
 <label>Damage Reason / Description *</label>
 <textarea rows="2" value={damagedTransferForm.damage_reason} onChange={(e) => setDamagedTransferForm(f => ({...f, damage_reason: e.target.value}))} required placeholder="Describe the damage in detail..."/>
 </div>
 <div className="form-group">
 <label>Action Taken</label>
 <select value={damagedTransferForm.action_taken} onChange={(e) => setDamagedTransferForm(f => ({...f, action_taken: e.target.value}))}>
 <option value="">Select action taken</option>
 <option value="Quarantine for Inspection">Quarantine for Inspection</option>
 <option value="Return to Supplier">Return to Supplier</option>
 <option value="Send for Repair">Send for Repair</option>
 <option value="Transfer to Disposal">Transfer to Disposal</option>
 <option value="Repackaging Required">Repackaging Required</option>
 <option value="Discount Sale">Discount Sale</option>
 <option value="Write Off">Write Off</option>
 <option value="Other">Other</option>
 </select>
 </div>
 <div className="form-group">
 <label>Notes</label>
 <textarea rows="2" value={damagedTransferForm.notes} onChange={(e) => setDamagedTransferForm(f => ({...f, notes: e.target.value}))} placeholder="Additional notes..."/>
 </div>
 <div className="modal-actions">
 <button type="button" className="btn btn-secondary" onClick={() => setShowDamagedTransferForm(false)}>Cancel</button>
 <button type="submit" className="btn btn-primary" disabled={damagedTransferLoading}>{damagedTransferLoading ? 'Processing...' : 'Create Damaged Transfer'}</button>
 </div>
 </form>
 </div>
 </div>
 </div>
 )}

 {/* Damaged Transfers Table */}
 <div className="table-container">
 <table className="data-table">
 <thead>
 <tr>
 <th>Transfer #</th>
 <th>From</th>
 <th>To</th>
 <th>Product</th>
 <th>Qty</th>
 <th>Damage Type</th>
 <th>Action Taken</th>
 <th>Status</th>
 <th>Created</th>
 <th>Actions</th>
 </tr>
 </thead>
 <tbody>
 {damagedTransfersList.map(t => (
 <tr key={t.id}>
 <td style={{fontWeight:600,color:'#f5576c'}}>{t.transfer_number}</td>
 <td>{t.from_warehouse_name}</td>
 <td>{t.to_warehouse_name}</td>
 <td>{t.product_name} {t.product_sku && <span style={{fontSize:10,color:'#999'}}>({t.product_sku})</span>}</td>
 <td style={{fontWeight:600}}>{t.quantity.toLocaleString()}</td>
 <td><span style={{background:'#fce4ec',color:'#c62828',padding:'2px 8px',borderRadius:12,fontSize:11}}>{t.damage_type}</span></td>
 <td>{t.action_taken || '-'}</td>
 <td><span style={{
 background: t.status === 'pending' ? '#fff3e0' : t.status === 'dispatched' ? '#e3f2fd' : t.status === 'received' ? '#e8f5e9' : '#fce4ec',
 color: t.status === 'pending' ? '#e65100' : t.status === 'dispatched' ? '#1565c0' : t.status === 'received' ? '#2e7d32' : '#c62828',
 padding:'3px 10px',borderRadius:12,fontSize:11,fontWeight:600
 }}>{t.status.toUpperCase()}</span></td>
 <td>{t.created_at ? new Date(t.created_at).toLocaleDateString() : 'N/A'}</td>
 <td>
 {t.status === 'pending' && (
 <button className="btn btn-sm" style={{fontSize:10,padding:'3px 8px',background:'#1565c0',color:'#fff'}} onClick={async () => {
 try {
 const token = localStorage.getItem('access_token');
 const headers = { 'Content-Type': 'application/json' };
 if (token) headers['Authorization'] = `Bearer ${token}`;
 const res = await authedFetch(`/api/damaged-transfers/${t.id}/dispatch`, { method: 'POST', headers });
 const d = await res.json();
 if (!res.ok) throw new Error(d.detail || 'Dispatch failed');
 notify(d.message, 'success');
 fetchDamagedTransfers(); fetchDamagedTransfersSummary();
 } catch(err) { notify(err.message, 'error'); }
 }}>Dispatch</button>
 )}
 {(t.status === 'pending' || t.status === 'dispatched') && (
 <button className="btn btn-sm" style={{fontSize:10,padding:'3px 8px',background:'#2e7d32',color:'#fff',marginLeft:4}} onClick={async () => {
 if (!window.confirm('Confirm receipt of this damaged transfer?')) return;
 try {
 const token = localStorage.getItem('access_token');
 const headers = { 'Content-Type': 'application/json' };
 if (token) headers['Authorization'] = `Bearer ${token}`;
 const res = await authedFetch(`/api/damaged-transfers/${t.id}/receive`, {
 method: 'POST', headers,
 body: JSON.stringify({ receipt_notes: '', receipt_condition: 'as_expected' })
 });
 const d = await res.json();
 if (!res.ok) throw new Error(d.detail || 'Receive failed');
 notify(d.message, 'success');
 fetchDamagedTransfers(); fetchDamagedTransfersSummary();
 } catch(err) { notify(err.message, 'error'); }
 }}>Receive</button>
 )}
 {t.status === 'received' && <span style={{fontSize:10,color:'#2e7d32',fontWeight:600}}>Completed</span>}
 </td>
 </tr>
 ))}
 {damagedTransfersList.length === 0 && (
 <tr><td colSpan="10" style={{textAlign:'center',padding:30,color:'#aaa'}}>No damaged transfers yet. Click "New Damaged Transfer" to move damaged products between warehouses.</td></tr>
 )}
 </tbody>
 </table>
 </div>
 </div>
 )}

 {/* ============ RECEIVE TRANSFERS MODULE ============ */}
 {activeModule === 'receiveTransfers' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Receive Transfers</h2>
 </div>
 <button onClick={() => { fetchReceiveTransfers(); fetchReceiveTransfersSummary(); }} className="btn btn-primary">Refresh</button>
 </div>

 {/* Summary Cards */}
 <div className="stats-cards" style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(180px,1fr))',gap:16,marginBottom:24}}>
 <div className="stat-card" style={{background:'linear-gradient(135deg,#fa709a 0%,#fee140 100%)',color:'#fff',borderRadius:12,padding:20}}>
 <div style={{fontSize:28,fontWeight:700}}>{receiveTransfersSummary.pending_damaged_transfers}</div>
 <div style={{fontSize:13,opacity:0.85}}>Awaiting Receipt</div>
 </div>
 <div className="stat-card" style={{background:'linear-gradient(135deg,#43e97b 0%,#38f9d7 100%)',color:'#fff',borderRadius:12,padding:20}}>
 <div style={{fontSize:28,fontWeight:700}}>{receiveTransfersSummary.received_damaged_transfers}</div>
 <div style={{fontSize:13,opacity:0.85}}>Received (Damaged)</div>
 </div>
 <div className="stat-card" style={{background:'linear-gradient(135deg,#667eea 0%,#764ba2 100%)',color:'#fff',borderRadius:12,padding:20}}>
 <div style={{fontSize:28,fontWeight:700}}>{receiveTransfersSummary.regular_completed_transfers}</div>
 <div style={{fontSize:13,opacity:0.85}}>Regular Transfers</div>
 </div>
 <div className="stat-card" style={{background:'linear-gradient(135deg,#4facfe 0%,#00f2fe 100%)',color:'#fff',borderRadius:12,padding:20}}>
 <div style={{fontSize:28,fontWeight:700}}>{receiveTransfersSummary.pending_receipt_quantity.toLocaleString()}</div>
 <div style={{fontSize:13,opacity:0.85}}>Units Awaiting</div>
 </div>
 <div className="stat-card" style={{background:'linear-gradient(135deg,#f093fb 0%,#f5576c 100%)',color:'#fff',borderRadius:12,padding:20}}>
 <div style={{fontSize:28,fontWeight:700}}>{receiveTransfersSummary.total_all_transfers}</div>
 <div style={{fontSize:13,opacity:0.85}}>Total All Transfers</div>
 </div>
 </div>

 {/* Receive Modal */}
 {showReceiveModal && (
 <div className="modal-overlay">
 <div className="modal" style={{maxWidth:500}}>
 <div className="modal-header">
 <div className="modal-header-left"><img src="/company-logo.png" alt="AstroBSM" className="modal-logo" onError={(e) => { e.target.style.display = 'none'; }}/><h3>Confirm Receipt</h3></div>
 <button className="modal-close" onClick={() => setShowReceiveModal(null)}>x</button>
 </div>
 <div className="modal-content">
 <p style={{marginBottom:12}}><strong>Transfer:</strong> {showReceiveModal.transfer_number}</p>
 <p style={{marginBottom:12}}><strong>Product:</strong> {showReceiveModal.product_name} x {showReceiveModal.quantity}</p>
 <p style={{marginBottom:16}}><strong>From:</strong> {showReceiveModal.from_warehouse_name} <strong>To:</strong> {showReceiveModal.to_warehouse_name}</p>
 <form onSubmit={async (e) => {
 e.preventDefault();
 setReceiveLoading(true);
 try {
 const token = localStorage.getItem('access_token');
 const headers = { 'Content-Type': 'application/json' };
 if (token) headers['Authorization'] = `Bearer ${token}`;
 const res = await authedFetch(`/api/receive-transfers/${showReceiveModal.id}/receive`, {
 method: 'POST', headers,
 body: JSON.stringify(receiveForm)
 });
 const d = await res.json();
 if (!res.ok) throw new Error(d.detail || 'Receive failed');
 notify(d.message, 'success');
 setShowReceiveModal(null);
 setReceiveForm({ receipt_notes: '', receipt_condition: 'as_expected' });
 fetchReceiveTransfers(); fetchReceiveTransfersSummary();
 } catch(err) { notify(err.message, 'error'); }
 finally { setReceiveLoading(false); }
 }}>
 <div className="form-group">
 <label>Receipt Condition</label>
 <select value={receiveForm.receipt_condition} onChange={(e) => setReceiveForm(f => ({...f, receipt_condition: e.target.value}))}>
 <option value="as_expected">As Expected</option>
 <option value="minor_additional_damage">Minor Additional Damage</option>
 <option value="major_additional_damage">Major Additional Damage</option>
 <option value="quantity_mismatch">Quantity Mismatch</option>
 <option value="wrong_product">Wrong Product</option>
 </select>
 </div>
 <div className="form-group">
 <label>Receipt Notes</label>
 <textarea rows="3" value={receiveForm.receipt_notes} onChange={(e) => setReceiveForm(f => ({...f, receipt_notes: e.target.value}))} placeholder="Notes about the received items..."/>
 </div>
 <div className="modal-actions">
 <button type="button" className="btn btn-secondary" onClick={() => setShowReceiveModal(null)}>Cancel</button>
 <button type="submit" className="btn btn-primary" disabled={receiveLoading}>{receiveLoading ? 'Processing...' : 'Confirm Receipt'}</button>
 </div>
 </form>
 </div>
 </div>
 </div>
 )}

 {/* Filter: Show pending first */}
 <div style={{marginBottom:12}}>
 <span style={{fontSize:12,color:'#888',fontStyle:'italic'}}>Transfers awaiting receipt are shown first. Use the "Receive" button to confirm receipt of damaged transfers.</span>
 </div>

 {/* Receive Transfers Table */}
 <div className="table-container">
 <table className="data-table">
 <thead>
 <tr>
 <th>Transfer #</th>
 <th>Type</th>
 <th>From</th>
 <th>To</th>
 <th>Product</th>
 <th>Qty</th>
 <th>Damage Type</th>
 <th>Status</th>
 <th>Date</th>
 <th>Received</th>
 <th>Actions</th>
 </tr>
 </thead>
 <tbody>
 {receiveTransfersList.sort((a,b) => {
 // Pending/dispatched first, then received, then completed
 const order = { pending: 0, dispatched: 1, received: 2, completed: 3 };
 return (order[a.status] || 9) - (order[b.status] || 9);
 }).map(t => (
 <tr key={`${t.transfer_type}-${t.id}`} style={{background: t.can_receive ? '#fffde7' : undefined}}>
 <td style={{fontWeight:600,color: t.transfer_type === 'damaged' ? '#f5576c' : '#667eea'}}>{t.transfer_number}</td>
 <td><span style={{
 background: t.transfer_type === 'damaged' ? '#fce4ec' : '#e8eaf6',
 color: t.transfer_type === 'damaged' ? '#c62828' : '#283593',
 padding:'2px 8px',borderRadius:12,fontSize:10,fontWeight:600
 }}>{t.transfer_type === 'damaged' ? 'DAMAGED' : 'REGULAR'}</span></td>
 <td>{t.from_warehouse_name}</td>
 <td>{t.to_warehouse_name}</td>
 <td>{t.product_name} {t.product_sku && <span style={{fontSize:10,color:'#999'}}>({t.product_sku})</span>}</td>
 <td style={{fontWeight:600}}>{t.quantity.toLocaleString()}</td>
 <td>{t.damage_type !== 'N/A' ? <span style={{background:'#fce4ec',color:'#c62828',padding:'2px 8px',borderRadius:12,fontSize:11}}>{t.damage_type}</span> : '-'}</td>
 <td><span style={{
 background: t.status === 'pending' ? '#fff3e0' : t.status === 'dispatched' ? '#e3f2fd' : t.status === 'received' ? '#e8f5e9' : '#e8f5e9',
 color: t.status === 'pending' ? '#e65100' : t.status === 'dispatched' ? '#1565c0' : '#2e7d32',
 padding:'3px 10px',borderRadius:12,fontSize:11,fontWeight:600
 }}>{t.status.toUpperCase()}</span></td>
 <td>{t.created_at ? new Date(t.created_at).toLocaleDateString() : 'N/A'}</td>
 <td>{t.received_at ? new Date(t.received_at).toLocaleDateString() : '-'}</td>
 <td>
 {t.can_receive ? (
 <button className="btn btn-sm" style={{fontSize:10,padding:'4px 10px',background:'linear-gradient(135deg,#43e97b 0%,#38f9d7 100%)',color:'#fff',border:'none',borderRadius:6,fontWeight:600}} onClick={() => {
 setShowReceiveModal(t);
 setReceiveForm({ receipt_notes: '', receipt_condition: 'as_expected' });
 }}>Receive</button>
 ) : (
 <span style={{fontSize:10,color:'#999'}}>{t.status === 'received' ? 'Received' : 'Complete'}</span>
 )}
 </td>
 </tr>
 ))}
 {receiveTransfersList.length === 0 && (
 <tr><td colSpan="11" style={{textAlign:'center',padding:30,color:'#aaa'}}>No transfers found. Transfers from the "Transfers" and "Damaged Transfers" modules will appear here.</td></tr>
 )}
 </tbody>
 </table>
 </div>
 </div>
 )}

 {/* Stock Management */}
 {activeModule === 'stockManagement' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Stock Management</h2>
 </div>
 </div>

 {/* Bulk Upload Options for Stock */}
 <div className="bulk-upload-section">
 <h3>Bulk Upload Options</h3>
 <div className="bulk-upload-buttons">
 <button onClick={() => setShowBulkUpload('productStockIntake')} className="btn btn-info">Bulk Product Intake</button>
 <button onClick={() => setShowBulkUpload('rawMaterialStockIntake')} className="btn btn-info">Bulk Raw Material Intake</button>
 <button onClick={() => setShowBulkUpload('warehouses')} className="btn btn-info">Bulk Warehouses</button>
 <button onClick={() => setShowBulkUpload('damagedProducts')} className="btn btn-info">Bulk Damaged Products</button>
 <button onClick={() => setShowBulkUpload('damagedRawMaterials')} className="btn btn-info">Bulk Damaged Raw Materials</button>
 <button onClick={() => setShowBulkUpload('productReturns')} className="btn btn-info">Bulk Product Returns</button>
 </div>
 </div>

 {/* Stock Management Action Buttons */}
 <div className="stock-actions-grid">
 <button className="action-btn product-intake" onClick={() => setShowForm('productStockIntake')}>
 Product Stock Intake
 </button>
 <button className="action-btn raw-material-intake" onClick={() => setShowForm('rawMaterialStockIntake')}>
 Raw Material Intake
 </button>
 <button className="action-btn warehouse" onClick={() => openForm('warehouse')}>
 Create New Warehouse
 </button>
 <button className="action-btn view-levels" onClick={() => viewProductStockLevels(false)}>
 Product Stock Levels
 </button>
 <button className="action-btn view-levels" onClick={() => viewRawMaterialStockLevels(false)}>
 Raw Material Levels
 </button>
 <button className="action-btn low-stock" onClick={() => viewProductStockLevels(true)}>
 Low Stock Products
 </button>
 <button className="action-btn low-stock" onClick={() => viewRawMaterialStockLevels(true)}>
 Low Stock Raw Materials
 </button>
 <button className="action-btn damaged" onClick={() => setShowForm('damagedProduct')}>
 Record Damaged Product
 </button>
 <button className="action-btn damaged" onClick={() => setShowForm('damagedRawMaterial')}>
 Damaged Raw Material
 </button>
 <button className="action-btn returned" onClick={() => setShowForm('returnedProduct')}>
 Record Product Return
 </button>
 <button className="action-btn analysis" onClick={() => viewStockAnalysis()}>
 Stock Analysis & Dashboard
 </button>
 </div>

 {/* Stock Display Area */}
 {stockDisplayArea && (
 <div className="stock-display-area" dangerouslySetInnerHTML={{ __html: stockDisplayArea }}></div>
 )}
 </div>
 )}

 {/* Reports */}
 {activeModule === 'reports' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Business Intelligence Dashboard</h2>
 </div>
 </div>

 {/* Key Metrics Summary Bar */}
 <div className="metrics-summary">
 <div className="metric-item metric-revenue">
 <div className="metric-icon"></div>
 <div className="metric-content">
 <div className="metric-label">Total Revenue</div>
 <div className="metric-value">{formatCurrency((data.sales||[]).filter(s=>s.payment_status==='paid').reduce((sum,s)=>sum+(parseFloat(s.total_amount)||0),0))}</div>
 <div className="metric-change positive">-> {(data.sales||[]).filter(s=>s.payment_status==='paid').length} paid orders</div>
 </div>
 </div>
 <div className="metric-item metric-pending">
 <div className="metric-icon"></div>
 <div className="metric-content">
 <div className="metric-label">Outstanding Payments</div>
 <div className="metric-value">{formatCurrency((data.sales||[]).filter(s=>s.payment_status==='unpaid').reduce((sum,s)=>sum+(parseFloat(s.total_amount)||0),0))}</div>
 <div className="metric-change negative"> {(data.sales||[]).filter(s=>s.payment_status==='unpaid').length} unpaid orders</div>
 </div>
 </div>
 <div className="metric-item metric-orders">
 <div className="metric-icon"></div>
 <div className="metric-content">
 <div className="metric-label">Total Orders</div>
 <div className="metric-value">{(data.sales||[]).length}</div>
 <div className="metric-change neutral">{(data.sales||[]).filter(s=>s.status==='pending').length} pending</div>
 </div>
 </div>
 <div className="metric-item metric-customers">
 <div className="metric-icon"></div>
 <div className="metric-content">
 <div className="metric-label">Active Customers</div>
 <div className="metric-value">{(data.customers||[]).filter(c=>(data.sales||[]).some(s=>s.customer_id===c.id)).length}</div>
 <div className="metric-change neutral">of {(data.customers||[]).length} total</div>
 </div>
 </div>
 </div>

 <div className="reports-grid-modern">
 {/* Sales Performance Card */}
 <div className="report-card-modern sales-card">
 <div className="card-header">
 <h3><span className="card-icon"></span> Sales & Payments</h3>
 <span className="card-badge">{(data.sales||[]).length} Orders</span>
 </div>
 <div className="card-body">
 <div className="stat-row">
 <div className="stat-item">
 <span className="stat-icon success"></span>
 <div className="stat-details">
 <div className="stat-label">Paid Orders</div>
 <div className="stat-value">{(data.sales||[]).filter(s=>s.payment_status==='paid').length}</div>
 <div className="stat-amount">{formatCurrency((data.sales||[]).filter(s=>s.payment_status==='paid').reduce((sum,s)=>sum+(parseFloat(s.total_amount)||0),0))}</div>
 </div>
 </div>
 <div className="stat-item">
 <span className="stat-icon warning"></span>
 <div className="stat-details">
 <div className="stat-label">Unpaid Orders</div>
 <div className="stat-value danger">{(data.sales||[]).filter(s=>s.payment_status==='unpaid').length}</div>
 <div className="stat-amount danger">{formatCurrency((data.sales||[]).filter(s=>s.payment_status==='unpaid').reduce((sum,s)=>sum+(parseFloat(s.total_amount)||0),0))}</div>
 </div>
 </div>
 </div>
 <div className="progress-bar">
 <div className="progress-fill" style={{width: `${(data.sales||[]).length > 0 ? ((data.sales||[]).filter(s=>s.payment_status==='paid').length / (data.sales||[]).length * 100) : 0}%`}}></div>
 </div>
 <div className="progress-label">{(data.sales||[]).length > 0 ? Math.round((data.sales||[]).filter(s=>s.payment_status==='paid').length / (data.sales||[]).length * 100) : 0}% Payment Collection Rate</div>
 </div>
 </div>

 {/* Order Status Card */}
 <div className="report-card-modern orders-card">
 <div className="card-header">
 <h3><span className="card-icon"></span> Order Status</h3>
 <span className="card-badge processing">{(data.sales||[]).filter(s=>s.status==='pending').length} Pending</span>
 </div>
 <div className="card-body">
 <div className="stat-grid">
 <div className="stat-box">
 <div className="stat-number">{(data.sales||[]).filter(s=>s.status==='pending').length}</div>
 <div className="stat-text">Pending</div>
 </div>
 <div className="stat-box completed">
 <div className="stat-number">{(data.sales||[]).filter(s=>s.status==='completed').length}</div>
 <div className="stat-text">Completed</div>
 </div>
 <div className="stat-box processing">
 <div className="stat-number">{(data.sales||[]).filter(s=>s.status==='processing').length}</div>
 <div className="stat-text">Processing</div>
 </div>
 <div className="stat-box cancelled">
 <div className="stat-number">{(data.sales||[]).filter(s=>s.status==='cancelled').length}</div>
 <div className="stat-text">Cancelled</div>
 </div>
 </div>
 </div>
 </div>

 {/* Inventory Health Card */}
 <div className="report-card-modern inventory-card">
 <div className="card-header">
 <h3><span className="card-icon"></span> Inventory Health</h3>
 <span className="card-badge">{(data.products||[]).length + (data.rawMaterials||[]).length} Items</span>
 </div>
 <div className="card-body">
 <div className="inventory-stats">
 <div className="inventory-item">
 <div className="inventory-icon"></div>
 <div className="inventory-info">
 <div className="inventory-label">Finished Products</div>
 <div className="inventory-count">{(data.products||[]).length}</div>
 </div>
 </div>
 <div className="inventory-item">
 <div className="inventory-icon"></div>
 <div className="inventory-info">
 <div className="inventory-label">Raw Materials</div>
 <div className="inventory-count">{(data.rawMaterials||[]).length}</div>
 </div>
 </div>
 <div className="inventory-item">
 <div className="inventory-icon"></div>
 <div className="inventory-info">
 <div className="inventory-label">Warehouses</div>
 <div className="inventory-count">{(data.warehouses||[]).length}</div>
 </div>
 </div>
 <div className="inventory-item">
 <div className="inventory-icon"></div>
 <div className="inventory-info">
 <div className="inventory-label">Stock Levels</div>
 <div className="inventory-count">{(data.stock||[]).length}</div>
 </div>
 </div>
 </div>
 </div>
 </div>

 {/* Staff & Operations Card */}
 <div className="report-card-modern staff-card">
 <div className="card-header">
 <h3><span className="card-icon"></span> Workforce & Operations</h3>
 <span className="card-badge active">{(data.attendance||[]).filter(a=>a.clock_in&&!a.clock_out).length} Active Now</span>
 </div>
 <div className="card-body">
 <div className="staff-metrics">
 <div className="staff-metric">
 <div className="metric-circle">
 <div className="circle-value">{(data.staff||[]).length}</div>
 </div>
 <div className="metric-label">Total Staff</div>
 </div>
 <div className="staff-details">
 <div className="detail-row">
 <span className="detail-icon"></span>
 <span className="detail-label">Clocked In Today</span>
 <span className="detail-value">{(data.attendance||[]).filter(a=>a.clock_in&&!a.clock_out).length}</span>
 </div>
 <div className="detail-row">
 <span className="detail-icon"></span>
 <span className="detail-label">Attendance Records</span>
 <span className="detail-value">{(data.attendance||[]).length}</span>
 </div>
 <div className="detail-row">
 <span className="detail-icon"></span>
 <span className="detail-label">Upcoming Birthdays</span>
 <span className="detail-value">{upcomingBirthdays.length}</span>
 </div>
 </div>
 </div>
 {/* Birthday list inside Workforce card */}
 {upcomingBirthdays.length > 0 && (
 <div style={{marginTop:12,borderTop:'1px solid #eee',paddingTop:12}}>
 <div style={{fontWeight:600,fontSize:13,marginBottom:8,color:'#856404'}}>Upcoming Birthdays:</div>
 {upcomingBirthdays.slice(0,5).map(b => (
 <div key={b.id || b.employee_id} style={{display:'flex',alignItems:'center',gap:8,padding:'4px 0',fontSize:13}}>
 <span></span>
 <span style={{fontWeight:600}}>{b.first_name} {b.last_name}</span>
 <span style={{color:'#888',marginLeft:'auto'}}>{b.is_today ? 'Today!' : `in ${b.days_until} day${b.days_until>1?'s':''}`}</span>
 </div>
 ))}
 {upcomingBirthdays.length > 5 && <div style={{fontSize:12,color:'#888',marginTop:4}}>+{upcomingBirthdays.length-5} more</div>}
 </div>
 )}
 </div>
 </div>

 {/* Production Overview Card */}
 <div className="report-card-modern production-card">
 <div className="card-header">
 <h3><span className="card-icon"></span> Production Pipeline</h3>
 <span className="card-badge">{(data.production||[]).filter(p=>p.status==='in_progress').length} In Progress</span>
 </div>
 <div className="card-body">
 <div className="production-flow">
 <div className="flow-stage">
 <div className="stage-count">{(data.production||[]).filter(p=>p.status==='pending').length}</div>
 <div className="stage-label">Pending</div>
 <div className="stage-bar pending"></div>
 </div>
 <div className="flow-arrow"></div>
 <div className="flow-stage">
 <div className="stage-count">{(data.production||[]).filter(p=>p.status==='in_progress').length}</div>
 <div className="stage-label">In Progress</div>
 <div className="stage-bar progress"></div>
 </div>
 <div className="flow-arrow"></div>
 <div className="flow-stage">
 <div className="stage-count">{(data.production||[]).filter(p=>p.status==='completed').length}</div>
 <div className="stage-label">Completed</div>
 <div className="stage-bar completed"></div>
 </div>
 </div>
 <div className="production-total">
 <strong>Total Production Orders:</strong> {(data.production||[]).length}
 </div>
 </div>
 </div>

 {/* Customer Analytics Card */}
 <div className="report-card-modern customer-card">
 <div className="card-header">
 <h3><span className="card-icon"></span> Customer Analytics</h3>
 <span className="card-badge">{(data.customers||[]).length} Customers</span>
 </div>
 <div className="card-body">
 <div className="customer-stats">
 <div className="customer-stat-item active">
 <div className="customer-stat-value">{(() => {
 const activeCount = (data.customers||[]).filter(c=>(data.sales||[]).some(s=>s.customer_id===c.id)).length;
 return activeCount;
 })()}</div>
 <div className="customer-stat-label">Active Customers</div>
 <div className="customer-stat-bar" style={{width: (() => {
 const total = (data.customers||[]).length;
 if (total === 0) return '0%';
 const activeCount = (data.customers||[]).filter(c=>(data.sales||[]).some(s=>s.customer_id===c.id)).length;
 const percentage = (activeCount / total * 100).toFixed(0);
 return `${percentage}%`;
 })()}}></div>
 </div>
 <div className="customer-stat-item inactive">
 <div className="customer-stat-value">{(() => {
 const total = (data.customers||[]).length;
 const activeCount = (data.customers||[]).filter(c=>(data.sales||[]).some(s=>s.customer_id===c.id)).length;
 return total - activeCount;
 })()}</div>
 <div className="customer-stat-label">Inactive Customers</div>
 <div className="customer-stat-bar" style={{width: (() => {
 const total = (data.customers||[]).length;
 if (total === 0) return '0%';
 const activeCount = (data.customers||[]).filter(c=>(data.sales||[]).some(s=>s.customer_id===c.id)).length;
 const inactiveCount = total - activeCount;
 const percentage = (inactiveCount / total * 100).toFixed(0);
 return `${percentage}%`;
 })()}}></div>
 </div>
 <div className="customer-credit">
 <div className="credit-label">Total Credit Limit</div>
 <div className="credit-value">{formatCurrency((data.customers||[]).reduce((sum,c)=>sum+(parseFloat(c.credit_limit)||0),0))}</div>
 </div>
 </div>
 </div>
 </div>
 </div>
 </div>
 )}

 {/* Settings */}
 {/* Financial Dashboard (Admin Only) */}
 {activeModule === 'financial' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Company Financial Status</h2>
 </div>
 <div className="module-header-actions">
 <button onClick={fetchFinancialData} className="btn btn-secondary">Refresh Data</button>
 <button onClick={exportFinancialPDF} className="btn btn-primary">Export PDF</button>
 </div>
 </div>

 {!financialData ? (
 <div style={{textAlign: 'center', padding: '60px'}}>
 <button onClick={fetchFinancialData} className="btn btn-primary btn-lg">Load Financial Dashboard</button>
 </div>
 ) : (
 <div className="financial-dashboard">
 {/* Financial Summary Cards */}
 <div className="financial-summary-grid">
 <div className="financial-card revenue">
 <div className="card-icon"></div>
 <div className="card-content">
 <h3>Total Revenue</h3>
 <div className="card-value">{formatCurrency(financialData.total_revenue || 0)}</div>
 <div className="card-detail">From {financialData.paid_orders_count || 0} paid orders</div>
 </div>
 </div>

 <div className="financial-card outstanding">
 <div className="card-icon"></div>
 <div className="card-content">
 <h3>Outstanding Payments</h3>
 <div className="card-value danger">{formatCurrency(financialData.outstanding_payments || 0)}</div>
 <div className="card-detail">{financialData.unpaid_orders_count || 0} unpaid orders</div>
 </div>
 </div>

 <div className="financial-card inventory">
 <div className="card-icon"></div>
 <div className="card-content">
 <h3>Inventory Value</h3>
 <div className="card-value">{formatCurrency(financialData.total_inventory_value || 0)}</div>
 <div className="card-detail">{financialData.total_products_in_stock || 0} products + {financialData.total_raw_materials_in_stock || 0} raw materials</div>
 </div>
 </div>

 <div className="financial-card networth">
 <div className="card-icon"></div>
 <div className="card-content">
 <h3>Company Net Worth</h3>
 <div className="card-value success">{formatCurrency(financialData.net_worth || 0)}</div>
 <div className="card-detail">Revenue + Inventory - Payroll</div>
 </div>
 </div>
 </div>

 {/* Detailed Breakdown */}
 <div className="financial-details-grid">
 {/* Cash Flow */}
 <div className="detail-card">
 <h3>Cash Flow Analysis</h3>
 <table className="detail-table">
 <tbody>
 <tr>
 <td>Total Revenue (Paid Orders)</td>
 <td className="amount-positive">{formatCurrency(financialData.total_revenue || 0)}</td>
 </tr>
 <tr>
 <td>Outstanding Receivables</td>
 <td className="amount-warning">{formatCurrency(financialData.outstanding_payments || 0)}</td>
 </tr>
 <tr>
 <td>Monthly Payroll Expense</td>
 <td className="amount-warning">{formatCurrency(financialData.monthly_payroll || 0)}</td>
 </tr>
 <tr>
 <td>Inventory Investment</td>
 <td className="amount-neutral">{formatCurrency(financialData.total_inventory_value || 0)}</td>
 </tr>
 <tr className="total-row">
 <td><strong>Net Cash Position</strong></td>
 <td className="amount-positive"><strong>{formatCurrency((financialData.total_revenue || 0) - (financialData.monthly_payroll || 0))}</strong></td>
 </tr>
 </tbody>
 </table>
 </div>

 {/* Inventory Breakdown */}
 <div className="detail-card">
 <h3>Inventory Breakdown</h3>
 <table className="detail-table">
 <tbody>
 <tr>
 <td>Product Inventory Value</td>
 <td className="amount-neutral">{formatCurrency(financialData.product_inventory_value || 0)}</td>
 </tr>
 <tr>
 <td>Raw Material Inventory Value</td>
 <td className="amount-neutral">{formatCurrency(financialData.raw_materials_value || 0)}</td>
 </tr>
 <tr>
 <td>Products in Stock</td>
 <td>{financialData.total_products_in_stock || 0} items ({Math.round(financialData.total_product_units || 0)} units)</td>
 </tr>
 <tr>
 <td>Raw Materials in Stock</td>
 <td>{financialData.total_raw_materials_in_stock || 0} items ({Math.round(financialData.total_raw_material_units || 0)} units)</td>
 </tr>
 <tr className="total-row">
 <td><strong>Total Inventory Value</strong></td>
 <td className="amount-neutral"><strong>{formatCurrency(financialData.total_inventory_value || 0)}</strong></td>
 </tr>
 </tbody>
 </table>
 </div>

 {/* Production Analysis */}
 <div className="detail-card">
 <h3>Production Analysis</h3>
 <table className="detail-table">
 <tbody>
 <tr>
 <td>Total Production Orders</td>
 <td>{financialData.total_production_orders || 0}</td>
 </tr>
 <tr>
 <td>Completed Orders</td>
 <td>{financialData.completed_production || 0}</td>
 </tr>
 <tr>
 <td>In Progress Orders</td>
 <td>{financialData.in_progress_production || 0}</td>
 </tr>
 <tr>
 <td>Pending Orders</td>
 <td>{financialData.pending_production || 0}</td>
 </tr>
 </tbody>
 </table>
 </div>

 {/* Sales Performance */}
 <div className="detail-card">
 <h3>Sales Performance</h3>
 <table className="detail-table">
 <tbody>
 <tr>
 <td>Total Orders</td>
 <td>{financialData.total_sales || 0}</td>
 </tr>
 <tr>
 <td>Paid Orders</td>
 <td className="amount-positive">{financialData.paid_sales || 0}</td>
 </tr>
 <tr>
 <td>Unpaid Orders</td>
 <td className="amount-warning">{financialData.unpaid_orders_count || 0}</td>
 </tr>
 <tr>
 <td>Total Customers</td>
 <td>{financialData.total_customers || 0}</td>
 </tr>
 <tr className="total-row">
 <td><strong>Payment Collection Rate</strong></td>
 <td><strong>{financialData.payment_collection_rate || 0}%</strong></td>
 </tr>
 </tbody>
 </table>
 </div>
 </div>

 {/* Profit Margin Analysis */}
 <div className="profit-analysis">
 <h3>Profitability Overview</h3>
 <div className="profit-grid">
 <div className="profit-metric">
 <div className="metric-label">Gross Revenue</div>
 <div className="metric-value">{formatCurrency(financialData.total_revenue || 0)}</div>
 </div>
 <div className="profit-separator">-</div>
 <div className="profit-metric">
 <div className="metric-label">Total Expenses</div>
 <div className="metric-value">{formatCurrency(financialData.total_expenses || 0)}</div>
 </div>
 <div className="profit-separator">=</div>
 <div className="profit-metric highlight">
 <div className="metric-label">Net Profit</div>
 <div className="metric-value">{formatCurrency(financialData.net_profit || 0)}</div>
 </div>
 </div>
 <div className="profit-note">
 <strong>Profit Margin:</strong> {financialData.profit_margin || 0}% | <strong>Monthly Payroll:</strong> {formatCurrency(financialData.monthly_payroll || 0)}
 </div>
 </div>

 {/* Product Inventory Breakdown */}
 {financialData.product_breakdown && financialData.product_breakdown.length > 0 && (
 <div className="detail-card" style={{marginTop: '20px'}}>
 <h3>Product Inventory Breakdown</h3>
 <table className="detail-table">
 <thead>
 <tr>
 <th style={{textAlign: 'left'}}>Product</th>
 <th style={{textAlign: 'center'}}>Unit</th>
 <th style={{textAlign: 'right'}}>Stock</th>
 <th style={{textAlign: 'right'}}>Cost/Unit</th>
 <th style={{textAlign: 'right'}}>Total Value</th>
 </tr>
 </thead>
 <tbody>
 {financialData.product_breakdown.map((item, idx) => (
 <tr key={idx}>
 <td>{item.name}</td>
 <td style={{textAlign: 'center'}}>{item.unit}</td>
 <td style={{textAlign: 'right'}}>{item.stock}</td>
 <td style={{textAlign: 'right'}}>{formatCurrency(item.unit_cost)}</td>
 <td style={{textAlign: 'right'}}>{formatCurrency(item.value)}</td>
 </tr>
 ))}
 <tr className="total-row">
 <td colSpan="4"><strong>Total Product Inventory</strong></td>
 <td style={{textAlign: 'right'}}><strong>{formatCurrency(financialData.product_inventory_value || 0)}</strong></td>
 </tr>
 </tbody>
 </table>
 </div>
 )}
 </div>
 )}
 </div>
 )}

 {/* =================== PRODUCTION CONSUMABLES =================== */}
 {activeModule === 'consumables' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Production Consumables</h2>
 </div>
 <button onClick={() => { fetchConsumables(); fetchLowStockConsumables(); }} className="btn btn-secondary">Refresh</button>
 </div>

 {/* Low Stock Alert Banner */}
 {lowStockConsumables.length > 0 && (
 <div style={{background: '#fff3cd', border: '1px solid #ffc107', borderRadius: '8px', padding: '15px', marginBottom: '20px'}}>
 <h4 style={{color: '#856404', marginBottom: '10px'}}>LOW STOCK ALERT - {lowStockConsumables.length} item(s) need restocking</h4>
 <div style={{display: 'flex', flexWrap: 'wrap', gap: '10px'}}>
 {lowStockConsumables.map(c => (
 <div key={c.id} style={{background: c.current_stock === 0 ? '#f8d7da' : '#fff', border: c.current_stock === 0 ? '2px solid #dc3545' : '1px solid #ffc107', borderRadius: '6px', padding: '10px 15px', minWidth: '200px'}}>
 <strong>{c.name}</strong><br/>
 <span style={{color: c.current_stock === 0 ? '#dc3545' : '#856404'}}>
 Stock: {c.current_stock} {c.unit} {c.current_stock === 0 ? '(OUT OF STOCK)' : `(Reorder at: ${c.reorder_level})`}
 </span><br/>
 <button onClick={() => { setRestockModal(c); setRestockQty(''); }} className="btn btn-primary" style={{marginTop: '5px', padding: '3px 10px', fontSize: '12px'}}>Restock Now</button>
 </div>
 ))}
 </div>
 </div>
 )}

 {/* Add/Edit Consumable Form */}
 <div className="card" style={{marginBottom: '20px', padding: '20px'}}>
 <h3>{editingConsumable ? 'Edit Consumable' : 'Register New Consumable'}</h3>
 <form onSubmit={saveConsumable}>
 <div className="form-row">
 <div className="form-group"><label>Name *</label><input value={consumableForm.name} onChange={(e) => setConsumableForm(p => ({...p, name: e.target.value}))} required placeholder="e.g., Packaging Film"/></div>
 <div className="form-group"><label>Unit *</label>
 <select value={consumableForm.unit} onChange={(e) => setConsumableForm(p => ({...p, unit: e.target.value}))}>
 <option value="unit">Unit</option><option value="kg">kg</option><option value="L">Liter</option>
 <option value="roll">Roll</option><option value="pack">Pack</option><option value="piece">Piece</option>
 <option value="kWh">kWh</option><option value="meal">Meal</option><option value="hour">Hour</option>
 </select>
 </div>
 <div className="form-group"><label>Unit Cost (NGN) *</label><input type="number" step="0.01" value={consumableForm.unit_cost} onChange={(e) => setConsumableForm(p => ({...p, unit_cost: e.target.value}))} required placeholder="0.00"/></div>
 <div className="form-group"><label>Category</label>
 <select value={consumableForm.category} onChange={(e) => setConsumableForm(p => ({...p, category: e.target.value}))}>
 <option value="">Select</option><option value="Packaging">Packaging</option><option value="Energy">Energy</option>
 <option value="Catering">Catering</option><option value="Cleaning">Cleaning</option><option value="Labels">Labels</option>
 <option value="Other">Other</option>
 </select>
 </div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Current Stock</label><input type="number" step="0.01" value={consumableForm.current_stock} onChange={(e) => setConsumableForm(p => ({...p, current_stock: e.target.value}))} placeholder="0" /></div>
 <div className="form-group"><label>Reorder Level</label><input type="number" step="0.01" value={consumableForm.reorder_level} onChange={(e) => setConsumableForm(p => ({...p, reorder_level: e.target.value}))} placeholder="Minimum before alert" /></div>
 <div className="form-group" style={{flex: 2}}><label>Description</label><input value={consumableForm.description} onChange={(e) => setConsumableForm(p => ({...p, description: e.target.value}))} placeholder="Optional description"/></div>
 <div className="form-group" style={{display: 'flex', gap: '8px', alignItems: 'flex-end'}}>
 <button type="submit" className="btn btn-primary" disabled={loading}>{loading ? 'Saving...' : (editingConsumable ? 'Update' : 'Add')} Consumable</button>
 {editingConsumable && <button type="button" className="btn btn-secondary" onClick={() => { setEditingConsumable(null); setConsumableForm({ name: '', unit: 'unit', unit_cost: '', category: '', description: '', current_stock: '', reorder_level: '' }); }}>Cancel</button>}
 </div>
 </div>
 </form>
 </div>

 {/* Consumables Table */}
 <div className="card" style={{padding: '20px'}}>
 <h3>Registered Consumables ({prodConsumables.length})</h3>
 <table className="data-table">
 <thead>
 <tr><th>Name</th><th>Category</th><th>Unit</th><th>Unit Cost</th><th>Current Stock</th><th>Reorder Level</th><th>Status</th><th>Actions</th></tr>
 </thead>
 <tbody>
 {prodConsumables.map(c => {
 const isLow = c.reorder_level > 0 && c.current_stock <= c.reorder_level;
 const isOut = c.current_stock === 0 && c.reorder_level > 0;
 return (
 <tr key={c.id} style={{background: isOut ? '#f8d7da' : isLow ? '#fff3cd' : 'transparent'}}>
 <td><strong>{c.name}</strong></td>
 <td><span className="badge">{c.category || 'N/A'}</span></td>
 <td>{c.unit}</td>
 <td>{formatCurrency(c.unit_cost)}</td>
 <td style={{fontWeight: 'bold', color: isOut ? '#dc3545' : isLow ? '#856404' : '#28a745'}}>{c.current_stock} {c.unit}</td>
 <td>{c.reorder_level > 0 ? `${c.reorder_level} ${c.unit}` : '-'}</td>
 <td>
 {isOut ? <span style={{color: '#dc3545', fontWeight: 'bold'}}>OUT OF STOCK</span>
 : isLow ? <span style={{color: '#856404', fontWeight: 'bold'}}>LOW STOCK</span>
 : <span style={{color: '#28a745'}}>OK</span>}
 </td>
 <td className="actions">
 <button onClick={() => { setRestockModal(c); setRestockQty(''); }} className="btn btn-primary" style={{padding: '3px 8px', fontSize: '12px', marginRight: '4px'}}>Restock</button>
 <button onClick={() => { setEditingConsumable(c); setConsumableForm({ name: c.name, unit: c.unit, unit_cost: c.unit_cost, category: c.category || '', description: c.description || '', current_stock: c.current_stock || 0, reorder_level: c.reorder_level || 0 }); }} className="btn-edit">Edit</button>
 <button onClick={() => deleteConsumable(c.id)} className="btn-danger" style={{marginLeft: '4px'}}>Delete</button>
 </td>
 </tr>
 );
 })}
 {prodConsumables.length === 0 && <tr><td colSpan="8" style={{textAlign: 'center', padding: '20px'}}>No consumables registered. Add one above.</td></tr>}
 </tbody>
 </table>
 </div>

 {/* Restock Modal */}
 {restockModal && (
 <div className="modal-overlay" onClick={() => setRestockModal(null)}>
 <div className="modal-content" onClick={(e) => e.stopPropagation()} style={{maxWidth: '400px'}}>
 <div className="modal-header">
 <h3>Restock: {restockModal.name}</h3>
 <button className="modal-close" onClick={() => setRestockModal(null)}>x</button>
 </div>
 <div style={{padding: '20px'}}>
 <p>Current Stock: <strong>{restockModal.current_stock} {restockModal.unit}</strong></p>
 <p>Reorder Level: <strong>{restockModal.reorder_level} {restockModal.unit}</strong></p>
 <div className="form-group" style={{marginTop: '15px'}}>
 <label>Quantity to Add ({restockModal.unit}) *</label>
 <input type="number" step="0.01" min="0.01" value={restockQty} onChange={(e) => setRestockQty(e.target.value)} placeholder="Enter quantity" autoFocus />
 </div>
 {restockQty > 0 && <p style={{color: '#28a745'}}>New stock will be: <strong>{(parseFloat(restockModal.current_stock || 0) + parseFloat(restockQty || 0)).toFixed(2)} {restockModal.unit}</strong></p>}
 <div style={{display: 'flex', gap: '10px', marginTop: '15px'}}>
 <button onClick={restockConsumable} className="btn btn-primary" disabled={loading || !restockQty || parseFloat(restockQty) <= 0}>{loading ? 'Restocking...' : 'Confirm Restock'}</button>
 <button onClick={() => setRestockModal(null)} className="btn btn-secondary">Cancel</button>
 </div>
 </div>
 </div>
 </div>
 )}
 </div>
 )}

 {/* =================== PRODUCTION COMPLETIONS =================== */}
 {activeModule === 'productionCompletions' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Production Completions</h2>
 </div>
 <button onClick={fetchProdCompletions} className="btn btn-secondary">Refresh</button>
 </div>

 {/* Production Completion Form */}
 <div className="card" style={{marginBottom: '20px', padding: '20px'}}>
 <h3>Record Production Completion</h3>
 <form onSubmit={submitProdCompletion}>
 <div className="form-row">
 <div className="form-group">
 <label>Product *</label>
 <select value={prodCompletionForm.product_id} onChange={(e) => {
 const pid = e.target.value;
 setProdCompletionForm(p => ({...p, product_id: pid}));
 if (pid && prodCompletionForm.qty_produced) fetchBomMaterials(pid, prodCompletionForm.qty_produced);
 }} required>
 <option value="">Select product</option>
 {(data.products || []).map(p => <option key={p.id} value={p.id}>{p.name} - {p.sku}</option>)}
 </select>
 </div>
 <div className="form-group">
 <label>Production Date *</label>
 <input type="date" value={prodCompletionForm.production_date} onChange={(e) => {
 const d = e.target.value;
 setProdCompletionForm(p => ({...p, production_date: d}));
 if (d) fetchDailyStaffSummary(d);
 }} required/>
 </div>
 <div className="form-group">
 <label>Qty Produced *</label>
 <input type="number" value={prodCompletionForm.qty_produced} onChange={(e) => {
 const q = e.target.value;
 setProdCompletionForm(p => ({...p, qty_produced: q}));
 if (prodCompletionForm.product_id && q) fetchBomMaterials(prodCompletionForm.product_id, q);
 }} required placeholder="0"/>
 </div>
 <div className="form-group">
 <label>Qty Damaged</label>
 <input type="number" value={prodCompletionForm.qty_damaged} onChange={(e) => setProdCompletionForm(p => ({...p, qty_damaged: e.target.value}))} placeholder="0"/>
 </div>
 </div>

 {prodCompletionForm.qty_damaged > 0 && (
 <div className="form-row">
 <div className="form-group" style={{flex: 1}}>
 <label>Damage Notes</label>
 <input value={prodCompletionForm.damage_notes} onChange={(e) => setProdCompletionForm(p => ({...p, damage_notes: e.target.value}))} placeholder="Describe damage reason"/>
 </div>
 </div>
 )}

 {/* Auto-fetched Staff Summary */}
 <div className="card" style={{background: '#f0f4ff', padding: '15px', marginBottom: '15px'}}>
 <h4 style={{marginBottom: '10px'}}>Staff & Wages (Auto-fetched from Attendance)</h4>
 <div className="form-row">
 <div className="form-group">
 <label>Staff Count</label>
 <input type="number" value={prodCompletionForm.staff_count} readOnly style={{backgroundColor: '#e8e8e8'}}/>
 </div>
 <div className="form-group">
 <label>Total Hours Worked</label>
 <input type="number" step="0.01" value={prodCompletionForm.total_hours_worked} readOnly style={{backgroundColor: '#e8e8e8'}}/>
 </div>
 <div className="form-group">
 <label>Total Wages Paid (NGN)</label>
 <input type="number" step="0.01" value={prodCompletionForm.total_wages_paid} readOnly style={{backgroundColor: '#e8e8e8'}}/>
 </div>
 <div className="form-group" style={{alignSelf: 'flex-end'}}>
 <button type="button" className="btn btn-secondary" onClick={() => fetchDailyStaffSummary(prodCompletionForm.production_date)}>Fetch Staff Data</button>
 </div>
 </div>
 {prodStaffSummary && (
 <small style={{color: '#666'}}>Data for {prodStaffSummary.production_date}: {prodStaffSummary.staff_count} staff worked {prodStaffSummary.total_hours_worked} hrs total{prodStaffSummary.overtime_hours > 0 ? ` (incl. ${prodStaffSummary.overtime_hours} hrs overtime)` : ''} | Wages: NGN {Number(prodStaffSummary.total_wages_paid).toLocaleString()}</small>
 )}
 </div>

 {/* BOM Raw Materials */}
 {prodBomMaterials.length > 0 && (
 <div className="card" style={{background: '#f0fff4', padding: '15px', marginBottom: '15px'}}>
 <h4 style={{marginBottom: '10px'}}>Raw Materials (From BOM)</h4>
 <table className="data-table">
 <thead><tr><th>Material</th><th>Unit</th><th>Qty/Unit</th><th>Total Qty</th><th>Unit Cost</th><th>Total Cost</th></tr></thead>
 <tbody>
 {prodBomMaterials.map((m, i) => (
 <tr key={i}>
 <td>{m.name}</td><td>{m.unit}</td><td>{m.qty_per_unit}</td>
 <td>{m.total_qty}</td><td>{formatCurrency(m.unit_cost)}</td><td>{formatCurrency(m.total_cost)}</td>
 </tr>
 ))}
 <tr style={{fontWeight: 'bold', background: '#d4edda'}}>
 <td colSpan="5">Total Raw Material Cost</td>
 <td>{formatCurrency(prodBomMaterials.reduce((s, m) => s + m.total_cost, 0))}</td>
 </tr>
 </tbody>
 </table>
 </div>
 )}

 {/* Consumables Used */}
 <div className="card" style={{background: '#fff8f0', padding: '15px', marginBottom: '15px'}}>
 <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px'}}>
 <h4>Production Consumables Used</h4>
 <button type="button" className="btn btn-secondary" onClick={addCompletionConsumable}>Add Consumable</button>
 </div>
 {(prodCompletionForm.consumables || []).map((con, idx) => (
 <div key={idx} className="form-row" style={{marginBottom: '8px'}}>
 <div className="form-group">
 <select value={con.consumable_id} onChange={(e) => updateCompletionConsumable(idx, 'consumable_id', e.target.value)}>
 <option value="">Select consumable</option>
 {prodConsumables.map(c => <option key={c.id} value={c.id}>{c.name} ({formatCurrency(c.unit_cost)}/{c.unit})</option>)}
 </select>
 </div>
 <div className="form-group">
 <input type="number" step="0.01" value={con.quantity} onChange={(e) => updateCompletionConsumable(idx, 'quantity', e.target.value)} placeholder="Quantity"/>
 </div>
 <div className="form-group" style={{flex: 0}}>
 <button type="button" className="btn btn-danger" onClick={() => removeCompletionConsumable(idx)}>X</button>
 </div>
 </div>
 ))}
 {(!prodCompletionForm.consumables || prodCompletionForm.consumables.length === 0) && (
 <small style={{color: '#999'}}>No consumables added. Click "Add Consumable" above.</small>
 )}
 </div>

 {/* Energy & Lunch Costs */}
 <div className="form-row">
 <div className="form-group"><label>Energy Cost (NGN)</label><input type="number" step="0.01" value={prodCompletionForm.energy_cost} onChange={(e) => setProdCompletionForm(p => ({...p, energy_cost: e.target.value}))} placeholder="0.00"/></div>
 <div className="form-group"><label>Lunch Cost (NGN)</label><input type="number" step="0.01" value={prodCompletionForm.lunch_cost} onChange={(e) => setProdCompletionForm(p => ({...p, lunch_cost: e.target.value}))} placeholder="0.00"/></div>
 <div className="form-group"><label>Destination Warehouse</label>
 <select value={prodCompletionForm.warehouse_id} onChange={(e) => setProdCompletionForm(p => ({...p, warehouse_id: e.target.value}))}>
 <option value="">Select warehouse</option>
 {(data.warehouses || []).map(w => <option key={w.id} value={w.id}>{w.name}</option>)}
 </select>
 </div>
 </div>
 <div className="form-row">
 <div className="form-group" style={{flex: 1}}><label>Notes</label><input value={prodCompletionForm.notes} onChange={(e) => setProdCompletionForm(p => ({...p, notes: e.target.value}))} placeholder="Optional notes"/></div>
 </div>

 <div style={{marginTop: '15px'}}>
 <button type="submit" className="btn btn-primary" disabled={loading}>{loading ? 'Recording...' : 'Record Production Completion'}</button>
 </div>
 </form>
 </div>

 {/* Completions History Table */}
 <div className="card" style={{padding: '20px'}}>
 <h3>Production Completion History ({prodCompletions.length})</h3>
 <div className="table-container">
 <table className="data-table">
 <thead>
 <tr><th>Date</th><th>Product</th><th>Qty Produced</th><th>Damaged</th><th>Staff</th><th>Hours</th><th>Wages</th><th>RM Cost</th><th>Consumables</th><th>Energy</th><th>Lunch</th><th>Total Cost</th><th>Cost/Unit</th></tr>
 </thead>
 <tbody>
 {prodCompletions.map(pc => (
 <tr key={pc.id}>
 <td>{pc.production_date}</td>
 <td><strong>{pc.product_name}</strong></td>
 <td>{pc.qty_produced}</td>
 <td style={{color: pc.qty_damaged > 0 ? 'red' : 'inherit'}}>{pc.qty_damaged}</td>
 <td>{pc.staff_count}</td>
 <td>{Number(pc.total_hours_worked).toFixed(1)}h</td>
 <td>{formatCurrency(pc.total_wages_paid)}</td>
 <td>{formatCurrency(pc.raw_material_cost)}</td>
 <td>{formatCurrency(pc.consumables_cost)}</td>
 <td>{formatCurrency(pc.energy_cost)}</td>
 <td>{formatCurrency(pc.lunch_cost)}</td>
 <td style={{fontWeight: 'bold'}}>{formatCurrency(pc.total_production_cost)}</td>
 <td style={{fontWeight: 'bold', color: '#2563eb'}}>{formatCurrency(pc.cost_per_unit)}</td>
 </tr>
 ))}
 {prodCompletions.length === 0 && <tr><td colSpan="13" style={{textAlign: 'center', padding: '20px'}}>No production completions recorded yet.</td></tr>}
 </tbody>
 </table>
 </div>
 </div>
 </div>
 )}

 {/* =================== MACHINES & EQUIPMENT =================== */}
 {activeModule === 'machinesEquipment' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Machines & Equipment</h2>
 </div>
 <div style={{display: 'flex', gap: '8px'}}>
 {machineView !== 'list' && <button onClick={() => { setMachineView('list'); setSelectedMachine(null); }} className="btn btn-secondary">Back to List</button>}
 <button onClick={() => { setShowForm('machine'); setEditingMachine(null); setMachineForm({ name: '', equipment_type: '', serial_number: '', model: '', manufacturer: '', purchase_date: '', purchase_cost: '', current_value: '', depreciation_rate: '10', depreciation_method: 'straight_line', location: '', status: 'Operational', notes: '' }); }} className="btn btn-primary">Add Machine</button>
 <button onClick={() => { fetchMachines(); fetchMachinesDashboard(); }} className="btn btn-secondary">Refresh</button>
 </div>
 </div>

 {/* Dashboard Summary */}
 {machineView === 'list' && machinesDashboard && (
 <div className="stats-grid" style={{marginBottom: '20px'}}>
 <div className="stat-card"><h3>{machinesDashboard.total_machines || 0}</h3><p>Total Machines</p></div>
 <div className="stat-card" style={{borderColor: '#22c55e'}}><h3>{machinesDashboard.operational || 0}</h3><p>Operational</p></div>
 <div className="stat-card" style={{borderColor: '#f59e0b'}}><h3>{machinesDashboard.under_maintenance || 0}</h3><p>Under Maintenance</p></div>
 <div className="stat-card" style={{borderColor: '#ef4444'}}><h3>{machinesDashboard.out_of_service || 0}</h3><p>Out of Service</p></div>
 <div className="stat-card" style={{borderColor: '#6366f1'}}><h3>{formatCurrency(machinesDashboard.total_asset_value || 0)}</h3><p>Total Asset Value</p></div>
 <div className="stat-card" style={{borderColor: '#ec4899'}}><h3>{machinesDashboard.open_faults || 0}</h3><p>Open Faults</p></div>
 </div>
 )}

 {/* Machine List View */}
 {machineView === 'list' && (
 <div className="card" style={{padding: '20px'}}>
 <h3>Equipment Registry ({machines.length})</h3>
 <table className="data-table">
 <thead>
 <tr><th>Name</th><th>Type</th><th>Serial #</th><th>Model</th><th>Status</th><th>Location</th><th>Purchase Cost</th><th>Current Value</th><th>Actions</th></tr>
 </thead>
 <tbody>
 {machines.map(m => (
 <tr key={m.id}>
 <td><strong>{m.name}</strong></td>
 <td>{m.equipment_type || '-'}</td>
 <td>{m.serial_number || '-'}</td>
 <td>{m.model || '-'}</td>
 <td>
 <span className={`status-badge ${m.status === 'Operational' ? 'active' : m.status === 'Under Maintenance' ? 'pending' : 'locked'}`}>
 {m.status}
 </span>
 </td>
 <td>{m.location || '-'}</td>
 <td>{formatCurrency(m.purchase_cost)}</td>
 <td>{formatCurrency(m.current_value)}</td>
 <td className="actions" style={{display: 'flex', gap: '4px', flexWrap: 'wrap'}}>
 <button onClick={() => { setSelectedMachine(m); setMachineView('detail'); fetchMachineDetail(m.id); }} className="btn-edit">View</button>
 <button onClick={() => { setEditingMachine(m); setMachineForm({ name: m.name, equipment_type: m.equipment_type || '', serial_number: m.serial_number || '', model: m.model || '', manufacturer: m.manufacturer || '', purchase_date: m.purchase_date || '', purchase_cost: m.purchase_cost || '', current_value: m.current_value || '', depreciation_rate: m.depreciation_rate || '10', depreciation_method: m.depreciation_method || 'straight_line', location: m.location || '', status: m.status || 'Operational', notes: m.notes || '' }); setShowForm('machine'); }} className="btn-edit">Edit</button>
 <button onClick={() => deleteMachine(m.id)} className="btn-danger">Delete</button>
 </td>
 </tr>
 ))}
 {machines.length === 0 && <tr><td colSpan="9" style={{textAlign: 'center', padding: '20px'}}>No machines registered. Click "Add Machine" to start.</td></tr>}
 </tbody>
 </table>
 </div>
 )}

 {/* Machine Detail View */}
 {machineView === 'detail' && selectedMachine && (
 <div>
 <div className="card" style={{padding: '20px', marginBottom: '20px'}}>
 <h3>{selectedMachine.name}</h3>
 <div className="form-row">
 <div><strong>Type:</strong> {selectedMachine.equipment_type || 'N/A'}</div>
 <div><strong>Serial:</strong> {selectedMachine.serial_number || 'N/A'}</div>
 <div><strong>Model:</strong> {selectedMachine.model || 'N/A'}</div>
 <div><strong>Manufacturer:</strong> {selectedMachine.manufacturer || 'N/A'}</div>
 <div><strong>Status:</strong> <span className={`status-badge ${selectedMachine.status === 'Operational' ? 'active' : 'pending'}`}>{selectedMachine.status}</span></div>
 <div><strong>Location:</strong> {selectedMachine.location || 'N/A'}</div>
 </div>
 </div>

 {/* Depreciation Card */}
 {machineDepreciation && (
 <div className="card" style={{padding: '20px', marginBottom: '20px', background: '#f0f4ff'}}>
 <h4>Depreciation</h4>
 <div className="form-row">
 <div><strong>Method:</strong> {machineDepreciation.method}</div>
 <div><strong>Purchase Cost:</strong> {formatCurrency(machineDepreciation.purchase_cost)}</div>
 <div><strong>Current Value:</strong> {formatCurrency(machineDepreciation.current_value)}</div>
 <div><strong>Annual Depreciation:</strong> {formatCurrency(machineDepreciation.annual_depreciation)}</div>
 <div><strong>Age (years):</strong> {machineDepreciation.age_years}</div>
 <div><strong>Total Depreciation:</strong> {formatCurrency(machineDepreciation.total_depreciation)}</div>
 </div>
 </div>
 )}

 {/* Tabs for Maintenance / Faults */}
 <div style={{display: 'flex', gap: '10px', marginBottom: '15px'}}>
 <button className={`btn ${machineView === 'detail' ? 'btn-primary' : 'btn-secondary'}`} onClick={() => setMachineView('detail')}>Overview</button>
 <button className="btn btn-secondary" onClick={() => setMachineView('maintenance')}>Maintenance ({machineMaintenanceRecords.length})</button>
 <button className="btn btn-secondary" onClick={() => setMachineView('faults')}>Faults ({machineFaults.length})</button>
 </div>

 {/* Maintenance Records */}
 <div className="card" style={{padding: '20px', marginBottom: '20px'}}>
 <h4>Maintenance Records</h4>
 <table className="data-table">
 <thead><tr><th>Type</th><th>Scheduled</th><th>Completed</th><th>Description</th><th>Cost</th><th>Performed By</th><th>Status</th></tr></thead>
 <tbody>
 {machineMaintenanceRecords.map(mr => (
 <tr key={mr.id}>
 <td><span className="badge">{mr.maintenance_type}</span></td>
 <td>{mr.scheduled_date || '-'}</td>
 <td>{mr.completed_date || '-'}</td>
 <td>{mr.description}</td>
 <td>{formatCurrency(mr.cost)}</td>
 <td>{mr.performed_by || '-'}</td>
 <td><span className={`status-badge ${mr.status === 'Completed' ? 'active' : 'pending'}`}>{mr.status}</span></td>
 </tr>
 ))}
 {machineMaintenanceRecords.length === 0 && <tr><td colSpan="7" style={{textAlign: 'center'}}>No maintenance records</td></tr>}
 </tbody>
 </table>

 {/* Add Maintenance Form */}
 <div style={{marginTop: '15px', padding: '15px', background: '#f8f9fa', borderRadius: '8px'}}>
 <h5>Add Maintenance Record</h5>
 <form onSubmit={saveMaintenanceRecord}>
 <div className="form-row">
 <div className="form-group"><label>Type</label><select value={maintenanceForm.maintenance_type} onChange={(e) => setMaintenanceForm(p => ({...p, maintenance_type: e.target.value}))}><option value="routine">Routine</option><option value="restorative">Restorative</option><option value="preventive">Preventive</option><option value="emergency">Emergency</option></select></div>
 <div className="form-group"><label>Scheduled Date</label><input type="date" value={maintenanceForm.scheduled_date} onChange={(e) => setMaintenanceForm(p => ({...p, scheduled_date: e.target.value}))}/></div>
 <div className="form-group"><label>Cost (NGN)</label><input type="number" step="0.01" value={maintenanceForm.cost} onChange={(e) => setMaintenanceForm(p => ({...p, cost: e.target.value}))}/></div>
 <div className="form-group"><label>Performed By</label><input value={maintenanceForm.performed_by} onChange={(e) => setMaintenanceForm(p => ({...p, performed_by: e.target.value}))}/></div>
 </div>
 <div className="form-row">
 <div className="form-group" style={{flex: 1}}><label>Description *</label><input value={maintenanceForm.description} onChange={(e) => setMaintenanceForm(p => ({...p, description: e.target.value}))} required placeholder="Describe maintenance work"/></div>
 </div>
 <button type="submit" className="btn btn-primary" disabled={loading}>{loading ? 'Saving...' : 'Add Maintenance Record'}</button>
 </form>
 </div>
 </div>

 {/* Faults Records */}
 <div className="card" style={{padding: '20px'}}>
 <h4>Fault Reports</h4>
 <table className="data-table">
 <thead><tr><th>Date</th><th>Description</th><th>Severity</th><th>Status</th><th>Resolution</th><th>Downtime</th><th>Repair Cost</th><th>Reported By</th></tr></thead>
 <tbody>
 {machineFaults.map(f => (
 <tr key={f.id}>
 <td>{f.fault_date}</td>
 <td>{f.description}</td>
 <td><span className={`badge ${f.severity === 'Critical' ? 'badge-danger' : f.severity === 'High' ? 'badge-warning' : ''}`}>{f.severity}</span></td>
 <td><span className={`status-badge ${f.status === 'Resolved' ? 'active' : f.status === 'Open' ? 'locked' : 'pending'}`}>{f.status}</span></td>
 <td>{f.resolution || '-'}</td>
 <td>{f.downtime_hours ? `${f.downtime_hours}h` : '-'}</td>
 <td>{formatCurrency(f.repair_cost)}</td>
 <td>{f.reported_by || '-'}</td>
 </tr>
 ))}
 {machineFaults.length === 0 && <tr><td colSpan="8" style={{textAlign: 'center'}}>No fault reports</td></tr>}
 </tbody>
 </table>

 {/* Add Fault Form */}
 <div style={{marginTop: '15px', padding: '15px', background: '#fff5f5', borderRadius: '8px'}}>
 <h5>Report Fault</h5>
 <form onSubmit={saveFault}>
 <div className="form-row">
 <div className="form-group"><label>Fault Date</label><input type="date" value={faultForm.fault_date} onChange={(e) => setFaultForm(p => ({...p, fault_date: e.target.value}))}/></div>
 <div className="form-group"><label>Severity</label><select value={faultForm.severity} onChange={(e) => setFaultForm(p => ({...p, severity: e.target.value}))}><option value="Low">Low</option><option value="Medium">Medium</option><option value="High">High</option><option value="Critical">Critical</option></select></div>
 <div className="form-group"><label>Status</label><select value={faultForm.status} onChange={(e) => setFaultForm(p => ({...p, status: e.target.value}))}><option value="Open">Open</option><option value="In Progress">In Progress</option><option value="Resolved">Resolved</option></select></div>
 <div className="form-group"><label>Reported By</label><input value={faultForm.reported_by} onChange={(e) => setFaultForm(p => ({...p, reported_by: e.target.value}))}/></div>
 </div>
 <div className="form-row">
 <div className="form-group" style={{flex: 2}}><label>Description *</label><input value={faultForm.description} onChange={(e) => setFaultForm(p => ({...p, description: e.target.value}))} required placeholder="Describe the fault"/></div>
 <div className="form-group"><label>Downtime (hours)</label><input type="number" step="0.1" value={faultForm.downtime_hours} onChange={(e) => setFaultForm(p => ({...p, downtime_hours: e.target.value}))}/></div>
 <div className="form-group"><label>Repair Cost (NGN)</label><input type="number" step="0.01" value={faultForm.repair_cost} onChange={(e) => setFaultForm(p => ({...p, repair_cost: e.target.value}))}/></div>
 </div>
 <div className="form-row">
 <div className="form-group" style={{flex: 1}}><label>Resolution</label><input value={faultForm.resolution} onChange={(e) => setFaultForm(p => ({...p, resolution: e.target.value}))} placeholder="How was it fixed? (if resolved)"/></div>
 </div>
 <button type="submit" className="btn btn-primary" disabled={loading}>{loading ? 'Saving...' : 'Report Fault'}</button>
 </form>
 </div>
 </div>
 </div>
 )}

 {/* Machine Maintenance dedicated view */}
 {machineView === 'maintenance' && selectedMachine && (
 <div className="card" style={{padding: '20px'}}>
 <h3>Maintenance Records - {selectedMachine.name}</h3>
 <table className="data-table">
 <thead><tr><th>Type</th><th>Scheduled</th><th>Completed</th><th>Description</th><th>Cost</th><th>Performed By</th><th>Next Scheduled</th><th>Status</th></tr></thead>
 <tbody>
 {machineMaintenanceRecords.map(mr => (
 <tr key={mr.id}>
 <td><span className="badge">{mr.maintenance_type}</span></td>
 <td>{mr.scheduled_date || '-'}</td>
 <td>{mr.completed_date || '-'}</td>
 <td>{mr.description}</td>
 <td>{formatCurrency(mr.cost)}</td>
 <td>{mr.performed_by || '-'}</td>
 <td>{mr.next_scheduled || '-'}</td>
 <td><span className={`status-badge ${mr.status === 'Completed' ? 'active' : 'pending'}`}>{mr.status}</span></td>
 </tr>
 ))}
 {machineMaintenanceRecords.length === 0 && <tr><td colSpan="8" style={{textAlign: 'center'}}>No maintenance records yet</td></tr>}
 </tbody>
 </table>
 </div>
 )}

 {/* Machine Faults dedicated view */}
 {machineView === 'faults' && selectedMachine && (
 <div className="card" style={{padding: '20px'}}>
 <h3>Fault History - {selectedMachine.name}</h3>
 <table className="data-table">
 <thead><tr><th>Date</th><th>Description</th><th>Severity</th><th>Status</th><th>Resolution</th><th>Downtime</th><th>Repair Cost</th><th>Reported By</th></tr></thead>
 <tbody>
 {machineFaults.map(f => (
 <tr key={f.id}>
 <td>{f.fault_date}</td>
 <td>{f.description}</td>
 <td><span className={`badge ${f.severity === 'Critical' ? 'badge-danger' : f.severity === 'High' ? 'badge-warning' : ''}`}>{f.severity}</span></td>
 <td><span className={`status-badge ${f.status === 'Resolved' ? 'active' : f.status === 'Open' ? 'locked' : 'pending'}`}>{f.status}</span></td>
 <td>{f.resolution || '-'}</td>
 <td>{f.downtime_hours ? `${f.downtime_hours}h` : '-'}</td>
 <td>{formatCurrency(f.repair_cost)}</td>
 <td>{f.reported_by || '-'}</td>
 </tr>
 ))}
 {machineFaults.length === 0 && <tr><td colSpan="8" style={{textAlign: 'center'}}>No faults reported</td></tr>}
 </tbody>
 </table>
 </div>
 )}
 </div>
 )}

 {/* ==================== MARKETER MODULE ==================== */}
 {activeModule === 'marketing' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Marketer Dashboard</h2>
 </div>
 <div className="module-actions">
 {['dashboard','guide','responsibilities','plans','logs','facilities','proposals','performance','products'].map(v => (
 <button key={v} className={`btn ${mktView===v?'btn-primary':'btn-secondary'}`} onClick={() => setMktView(v)} style={{marginRight:6,marginBottom:4}}>
 {v === 'plans' ? 'Weekly Plans' : v === 'logs' ? 'Daily Logs' : v === 'proposals' ? 'Proposals' : v === 'products' ? 'Products Catalog' : v === 'guide' ? 'Best-Practice Guide' : v === 'responsibilities' ? 'Responsibilities' : v === 'facilities' ? 'Facilities & Contacts' : v === 'performance' ? 'Performance & Sales' : 'Dashboard'}
 </button>
 ))}
 </div>
 </div>

 {/* Marketing Dashboard View */}
 {mktView === 'dashboard' && mktDashboard && (
 <div>
 <div className="stats-grid" style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(200px,1fr))',gap:16,marginBottom:24}}>
 <div className="stat-card primary"><h4>Active Plans</h4><p className="stat-number">{mktDashboard.active_plans||0}</p></div>
 <div className="stat-card success"><h4>Today's Logs</h4><p className="stat-number">{mktDashboard.today_logs||0}</p></div>
 <div className="stat-card info"><h4>Active Proposals</h4><p className="stat-number">{mktDashboard.active_proposals||0}</p></div>
 <div className="stat-card warning"><h4>Pending Follow-ups</h4><p className="stat-number">{mktDashboard.pending_followups||0}</p></div>
 </div>
 {mktDashboard.week_stats && (
 <div style={{background:'#f8f9fa',borderRadius:8,padding:16,marginBottom:24}}>
 <h3 style={{marginBottom:12}}>This Week's Activity Summary</h3>
 <div className="stats-grid" style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(160px,1fr))',gap:12}}>
 <div className="stat-card"><h4>Total Logs</h4><p className="stat-number">{mktDashboard.week_stats.total_logs||0}</p></div>
 <div className="stat-card"><h4>Total Orders</h4><p className="stat-number">{mktDashboard.week_stats.total_orders||0}</p></div>
 <div className="stat-card"><h4>Order Value</h4><p className="stat-number">&#8358;{Number(mktDashboard.week_stats.total_order_value||0).toLocaleString()}</p></div>
 <div className="stat-card"><h4>Customers Contacted</h4><p className="stat-number">{mktDashboard.week_stats.customers_contacted||0}</p></div>
 <div className="stat-card"><h4>Samples Given</h4><p className="stat-number">{mktDashboard.week_stats.samples_given||0}</p></div>
 </div>
 </div>
 )}
 {mktDashboard.recent_logs && mktDashboard.recent_logs.length > 0 && (
 <div style={{marginBottom:24}}>
 <h3 style={{marginBottom:12}}>Recent Activity</h3>
 <div className="table-responsive"><table className="data-table"><thead><tr><th>Date</th><th>Marketer</th><th>Location</th><th>Customer</th><th>Outcome</th><th>Orders</th></tr></thead><tbody>
 {mktDashboard.recent_logs.map((l,i) => (
 <tr key={i}><td>{l.log_date}</td><td>{l.marketer_name||'-'}</td><td>{l.location_visited}</td><td>{l.customer_contacted}</td><td>{l.outcome}</td><td>{l.orders_generated}</td></tr>
 ))}
 </tbody></table></div>
 </div>
 )}
 </div>
 )}

 {/* Weekly Plans View */}
 {mktView === 'plans' && (
 <div>
 <div style={{background:'#fff',borderRadius:8,padding:20,boxShadow:'0 1px 3px rgba(0,0,0,.1)',marginBottom:24}}>
 <h3 style={{marginBottom:16}}>{editingMktPlan ? 'Edit Weekly Plan' : 'Submit Weekly Plan'}</h3>
 <form onSubmit={saveMktPlan}>
 <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(240px,1fr))',gap:12}}>
 <div className="form-group"><label>Marketer (Staff ID)</label>
 <select value={mktPlanForm.marketer_staff_id} onChange={e => setMktPlanForm({...mktPlanForm, marketer_staff_id: e.target.value})} required>
 <option value="">Select Staff</option>
 {(data.staff||[]).map(s => <option key={s.id} value={s.id}>{s.first_name} {s.last_name} ({s.employee_id})</option>)}
 </select>
 </div>
 <div className="form-group"><label>Week Start</label><input type="date" value={mktPlanForm.week_start} onChange={e => setMktPlanForm({...mktPlanForm, week_start: e.target.value})} required /></div>
 <div className="form-group"><label>Week End</label><input type="date" value={mktPlanForm.week_end} onChange={e => setMktPlanForm({...mktPlanForm, week_end: e.target.value})} required /></div>
 <div className="form-group"><label>Title</label><input value={mktPlanForm.title} onChange={e => setMktPlanForm({...mktPlanForm, title: e.target.value})} required /></div>
 <div className="form-group"><label>Planned Visits</label><input type="number" value={mktPlanForm.planned_visits} onChange={e => setMktPlanForm({...mktPlanForm, planned_visits: e.target.value})} /></div>
 <div className="form-group"><label>Planned Calls</label><input type="number" value={mktPlanForm.planned_calls} onChange={e => setMktPlanForm({...mktPlanForm, planned_calls: e.target.value})} /></div>
 <div className="form-group"><label>Budget Requested (NGN)</label><input type="number" step="0.01" value={mktPlanForm.budget_requested} onChange={e => setMktPlanForm({...mktPlanForm, budget_requested: e.target.value})} /></div>
 </div>
 <div className="form-group" style={{marginTop:12}}><label>Objectives</label><textarea rows={3} value={mktPlanForm.objectives} onChange={e => setMktPlanForm({...mktPlanForm, objectives: e.target.value})} required /></div>
 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12}}>
 <div className="form-group"><label>Target Areas</label><textarea rows={2} value={mktPlanForm.target_areas} onChange={e => setMktPlanForm({...mktPlanForm, target_areas: e.target.value})} /></div>
 <div className="form-group"><label>Target Customers</label><textarea rows={2} value={mktPlanForm.target_customers} onChange={e => setMktPlanForm({...mktPlanForm, target_customers: e.target.value})} /></div>
 </div>
 <div style={{display:'flex',gap:8,marginTop:12}}>
 <button type="submit" className="btn btn-primary" disabled={loading}>{editingMktPlan ? 'Update Plan' : 'Submit Plan'}</button>
 {editingMktPlan && <button type="button" className="btn btn-secondary" onClick={() => { setEditingMktPlan(null); setMktPlanForm({marketer_staff_id:'',week_start:'',week_end:'',title:'',objectives:'',target_areas:'',target_customers:'',planned_visits:'',planned_calls:'',budget_requested:'',status:'submitted'}); }}>Cancel</button>}
 </div>
 </form>
 </div>
 <div className="table-responsive"><table className="data-table"><thead><tr><th>Week</th><th>Title</th><th>Marketer</th><th>Objectives</th><th>Visits</th><th>Calls</th><th>Budget</th><th>Status</th><th>Actions</th></tr></thead><tbody>
 {mktPlans.map(p => (
 <tr key={p.id}>
 <td>{p.week_start} - {p.week_end}</td><td>{p.title}</td><td>{p.marketer_name||'-'}</td>
 <td style={{maxWidth:200,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{p.objectives}</td>
 <td>{p.planned_visits}</td><td>{p.planned_calls}</td><td>&#8358;{Number(p.budget_requested||0).toLocaleString()}</td>
 <td><span style={{padding:'2px 8px',borderRadius:12,fontSize:12,fontWeight:600,background: p.status==='approved'?'#d4edda':p.status==='submitted'?'#fff3cd':p.status==='rejected'?'#f8d7da':'#e2e3e5',color: p.status==='approved'?'#155724':p.status==='submitted'?'#856404':p.status==='rejected'?'#721c24':'#383d41'}}>{(p.status||'').toUpperCase()}</span></td>
 <td>
 <button className="btn btn-sm btn-secondary" style={{marginRight:4}} onClick={() => { setEditingMktPlan(p); setMktPlanForm({marketer_staff_id:p.marketer_staff_id,week_start:p.week_start,week_end:p.week_end,title:p.title,objectives:p.objectives,target_areas:p.target_areas||'',target_customers:p.target_customers||'',planned_visits:p.planned_visits||'',planned_calls:p.planned_calls||'',budget_requested:p.budget_requested||'',status:p.status||'submitted'}); }}>Edit</button>
 {(!currentUser || currentUser.role === 'admin') && p.status === 'submitted' && (
 <button className="btn btn-sm" style={{marginRight:4,background:'#27ae60',color:'#fff',border:'none',borderRadius:4,padding:'3px 10px',fontSize:12,cursor:'pointer'}} onClick={() => approveMktPlan(p.id, p.title, p.budget_requested)}>Approve</button>
 )}
 {(!currentUser || currentUser.role === 'admin') && p.status === 'submitted' && (
 <button className="btn btn-sm" style={{marginRight:4,background:'#e74c3c',color:'#fff',border:'none',borderRadius:4,padding:'3px 10px',fontSize:12,cursor:'pointer'}} onClick={async () => { if(!window.confirm('Reject this plan?')) return; try { const r = await authedFetch(`/api/marketing/plans/${p.id}`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:'rejected'})}); if(!r.ok) throw new Error('Failed'); notify('Plan rejected','success'); fetchMktPlans(); fetchMktDashboard(); } catch(e) { notify(e.message,'error'); } }}>Reject</button>
 )}
 <button className="btn btn-sm btn-danger" onClick={() => deleteMktPlan(p.id)}>Delete</button>
 </td>
 </tr>
 ))}
 {mktPlans.length === 0 && <tr><td colSpan={9} style={{textAlign:'center',padding:20}}>No weekly plans found</td></tr>}
 </tbody></table></div>
 </div>
 )}

 {/* Daily Logs View */}
 {mktView === 'logs' && (
 <div>
 <div style={{background:'#fff',borderRadius:8,padding:20,boxShadow:'0 1px 3px rgba(0,0,0,.1)',marginBottom:24}}>
 <h3 style={{marginBottom:16}}>{editingMktLog ? 'Edit Daily Log' : 'Submit Daily Activity Log'}</h3>
 <form onSubmit={saveMktLog}>
 <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(220px,1fr))',gap:12}}>
 <div className="form-group"><label>Marketer (Staff)</label>
 <select value={mktLogForm.marketer_staff_id} onChange={e => setMktLogForm({...mktLogForm, marketer_staff_id: e.target.value})} required>
 <option value="">Select Staff</option>
 {(data.staff||[]).map(s => <option key={s.id} value={s.id}>{s.first_name} {s.last_name} ({s.employee_id})</option>)}
 </select>
 </div>
 <div className="form-group"><label>Date</label><input type="date" value={mktLogForm.log_date} onChange={e => setMktLogForm({...mktLogForm, log_date: e.target.value})} required /></div>
 <div className="form-group"><label>Start Time</label><input type="time" value={mktLogForm.start_time} onChange={e => setMktLogForm({...mktLogForm, start_time: e.target.value})} /></div>
 <div className="form-group"><label>End Time</label><input type="time" value={mktLogForm.end_time} onChange={e => setMktLogForm({...mktLogForm, end_time: e.target.value})} /></div>
 <div className="form-group"><label>Location Visited</label><input value={mktLogForm.location_visited} onChange={e => setMktLogForm({...mktLogForm, location_visited: e.target.value})} required /></div>
 <div className="form-group"><label>Customer Contacted</label><input value={mktLogForm.customer_contacted} onChange={e => setMktLogForm({...mktLogForm, customer_contacted: e.target.value})} /></div>
 <div className="form-group"><label>Contact Type</label>
 <select value={mktLogForm.contact_type} onChange={e => setMktLogForm({...mktLogForm, contact_type: e.target.value})}>
 <option value="visit">Visit</option><option value="call">Phone Call</option><option value="email">Email</option><option value="whatsapp">WhatsApp</option><option value="meeting">Meeting</option><option value="other">Other</option>
 </select>
 </div>
 <div className="form-group"><label>Products Discussed</label><input value={mktLogForm.products_discussed} onChange={e => setMktLogForm({...mktLogForm, products_discussed: e.target.value})} /></div>
 <div className="form-group"><label>Samples Distributed</label><input type="number" value={mktLogForm.samples_distributed} onChange={e => setMktLogForm({...mktLogForm, samples_distributed: e.target.value})} /></div>
 <div className="form-group"><label>Orders Generated</label><input type="number" value={mktLogForm.orders_generated} onChange={e => setMktLogForm({...mktLogForm, orders_generated: e.target.value})} /></div>
 <div className="form-group"><label>Order Value (NGN)</label><input type="number" step="0.01" value={mktLogForm.order_value} onChange={e => setMktLogForm({...mktLogForm, order_value: e.target.value})} /></div>
 <div className="form-group"><label>Mood Rating (1-5)</label><input type="number" min="1" max="5" value={mktLogForm.mood_rating} onChange={e => setMktLogForm({...mktLogForm, mood_rating: e.target.value})} /></div>
 </div>
 <div className="form-group" style={{marginTop:12}}><label>Objective</label><textarea rows={2} value={mktLogForm.objective} onChange={e => setMktLogForm({...mktLogForm, objective: e.target.value})} required /></div>
 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12}}>
 <div className="form-group"><label>Activities Performed</label><textarea rows={3} value={mktLogForm.activities_performed} onChange={e => setMktLogForm({...mktLogForm, activities_performed: e.target.value})} required /></div>
 <div className="form-group"><label>Outcome</label><textarea rows={3} value={mktLogForm.outcome} onChange={e => setMktLogForm({...mktLogForm, outcome: e.target.value})} /></div>
 </div>
 <div className="form-group"><label>Challenges</label><textarea rows={2} value={mktLogForm.challenges} onChange={e => setMktLogForm({...mktLogForm, challenges: e.target.value})} /></div>
 <div style={{display:'grid',gridTemplateColumns:'auto 1fr 1fr',gap:12,alignItems:'end'}}>
 <div className="form-group"><label><input type="checkbox" checked={mktLogForm.follow_up_required} onChange={e => setMktLogForm({...mktLogForm, follow_up_required: e.target.checked})} style={{marginRight:6}} />Follow-up Required</label></div>
 {mktLogForm.follow_up_required && <>
 <div className="form-group"><label>Follow-up Date</label><input type="date" value={mktLogForm.follow_up_date} onChange={e => setMktLogForm({...mktLogForm, follow_up_date: e.target.value})} /></div>
 <div className="form-group"><label>Follow-up Notes</label><input value={mktLogForm.follow_up_notes} onChange={e => setMktLogForm({...mktLogForm, follow_up_notes: e.target.value})} /></div>
 </>}
 </div>
 <div style={{display:'flex',gap:8,marginTop:12}}>
 <button type="submit" className="btn btn-primary" disabled={loading}>{editingMktLog ? 'Update Log' : 'Submit Log'}</button>
 {editingMktLog && <button type="button" className="btn btn-secondary" onClick={() => { setEditingMktLog(null); setMktLogForm({marketer_staff_id:'',log_date:new Date().toISOString().split('T')[0],start_time:'',end_time:'',location_visited:'',customer_contacted:'',contact_type:'visit',objective:'',activities_performed:'',outcome:'',products_discussed:'',samples_distributed:'0',orders_generated:'0',order_value:'0',challenges:'',follow_up_required:false,follow_up_date:'',follow_up_notes:'',mood_rating:'3'}); }}>Cancel</button>}
 </div>
 </form>
 </div>
 <div className="table-responsive"><table className="data-table"><thead><tr><th>Date</th><th>Marketer</th><th>Location</th><th>Customer</th><th>Type</th><th>Outcome</th><th>Orders</th><th>Value</th><th>Follow-up</th><th>Actions</th></tr></thead><tbody>
 {mktLogs.map(l => (
 <tr key={l.id}>
 <td>{l.log_date}</td><td>{l.marketer_name||'-'}</td><td>{l.location_visited}</td><td>{l.customer_contacted}</td>
 <td>{l.contact_type}</td><td style={{maxWidth:150,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{l.outcome}</td>
 <td>{l.orders_generated}</td><td>&#8358;{Number(l.order_value||0).toLocaleString()}</td>
 <td>{l.follow_up_required ? <span style={{color:'#e67e22',fontWeight:600}}>Yes - {l.follow_up_date}</span> : 'No'}</td>
 <td>
 <button className="btn btn-sm btn-secondary" style={{marginRight:4}} onClick={() => { setEditingMktLog(l); setMktLogForm({marketer_staff_id:l.marketer_staff_id,log_date:l.log_date,start_time:l.start_time||'',end_time:l.end_time||'',location_visited:l.location_visited,customer_contacted:l.customer_contacted||'',contact_type:l.contact_type||'visit',objective:l.objective||'',activities_performed:l.activities_performed||'',outcome:l.outcome||'',products_discussed:l.products_discussed||'',samples_distributed:l.samples_distributed||'0',orders_generated:l.orders_generated||'0',order_value:l.order_value||'0',challenges:l.challenges||'',follow_up_required:l.follow_up_required||false,follow_up_date:l.follow_up_date||'',follow_up_notes:l.follow_up_notes||'',mood_rating:l.mood_rating||'3'}); }}>Edit</button>
 <button className="btn btn-sm btn-danger" onClick={() => deleteMktLog(l.id)}>Delete</button>
 </td>
 </tr>
 ))}
 {mktLogs.length === 0 && <tr><td colSpan={10} style={{textAlign:'center',padding:20}}>No daily logs found</td></tr>}
 </tbody></table></div>
 </div>
 )}

 {/* Proposals View */}
 {mktView === 'proposals' && (
 <div>
 <div style={{background:'#fff',borderRadius:8,padding:20,boxShadow:'0 1px 3px rgba(0,0,0,.1)',marginBottom:24}}>
 <h3 style={{marginBottom:16}}>{editingMktProposal ? 'Edit Proposal' : 'Submit Marketing Proposal'}</h3>
 <form onSubmit={saveMktProposal}>
 <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(240px,1fr))',gap:12}}>
 <div className="form-group"><label>Marketer (Staff)</label>
 <select value={mktProposalForm.marketer_staff_id} onChange={e => setMktProposalForm({...mktProposalForm, marketer_staff_id: e.target.value})} required>
 <option value="">Select Staff</option>
 {(data.staff||[]).map(s => <option key={s.id} value={s.id}>{s.first_name} {s.last_name} ({s.employee_id})</option>)}
 </select>
 </div>
 <div className="form-group"><label>Title</label><input value={mktProposalForm.title} onChange={e => setMktProposalForm({...mktProposalForm, title: e.target.value})} required /></div>
 <div className="form-group"><label>Proposal Type</label>
 <select value={mktProposalForm.proposal_type} onChange={e => setMktProposalForm({...mktProposalForm, proposal_type: e.target.value})}>
 <option value="campaign">Campaign</option><option value="promotion">Promotion</option><option value="event">Event</option><option value="partnership">Partnership</option><option value="digital">Digital Marketing</option><option value="branding">Branding</option><option value="other">Other</option>
 </select>
 </div>
 <div className="form-group"><label>Target Audience</label><input value={mktProposalForm.target_audience} onChange={e => setMktProposalForm({...mktProposalForm, target_audience: e.target.value})} /></div>
 <div className="form-group"><label>Budget Estimate (NGN)</label><input type="number" step="0.01" value={mktProposalForm.budget_estimate} onChange={e => setMktProposalForm({...mktProposalForm, budget_estimate: e.target.value})} /></div>
 <div className="form-group"><label>Timeline Start</label><input type="date" value={mktProposalForm.timeline_start} onChange={e => setMktProposalForm({...mktProposalForm, timeline_start: e.target.value})} /></div>
 <div className="form-group"><label>Timeline End</label><input type="date" value={mktProposalForm.timeline_end} onChange={e => setMktProposalForm({...mktProposalForm, timeline_end: e.target.value})} /></div>
 <div className="form-group"><label>Channels</label><input value={mktProposalForm.channels} onChange={e => setMktProposalForm({...mktProposalForm, channels: e.target.value})} placeholder="e.g. social media, print, radio" /></div>
 <div className="form-group"><label>Products Involved</label><input value={mktProposalForm.products_involved} onChange={e => setMktProposalForm({...mktProposalForm, products_involved: e.target.value})} /></div>
 </div>
 <div className="form-group" style={{marginTop:12}}><label>Description</label><textarea rows={3} value={mktProposalForm.description} onChange={e => setMktProposalForm({...mktProposalForm, description: e.target.value})} required /></div>
 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12}}>
 <div className="form-group"><label>Strategy</label><textarea rows={3} value={mktProposalForm.strategy} onChange={e => setMktProposalForm({...mktProposalForm, strategy: e.target.value})} /></div>
 <div className="form-group"><label>Expected Outcome</label><textarea rows={3} value={mktProposalForm.expected_outcome} onChange={e => setMktProposalForm({...mktProposalForm, expected_outcome: e.target.value})} /></div>
 </div>
 <div className="form-group"><label>KPI Metrics</label><textarea rows={2} value={mktProposalForm.kpi_metrics} onChange={e => setMktProposalForm({...mktProposalForm, kpi_metrics: e.target.value})} placeholder="How success will be measured" /></div>
 <div style={{display:'flex',gap:8,marginTop:12}}>
 <button type="submit" className="btn btn-primary" disabled={loading}>{editingMktProposal ? 'Update Proposal' : 'Submit Proposal'}</button>
 {editingMktProposal && <button type="button" className="btn btn-secondary" onClick={() => { setEditingMktProposal(null); setMktProposalForm({marketer_staff_id:'',title:'',proposal_type:'campaign',target_audience:'',description:'',strategy:'',expected_outcome:'',budget_estimate:'',timeline_start:'',timeline_end:'',products_involved:'',channels:'',kpi_metrics:'',status:'draft'}); }}>Cancel</button>}
 </div>
 </form>
 </div>
 <div className="table-responsive"><table className="data-table"><thead><tr><th>Title</th><th>Type</th><th>Marketer</th><th>Budget</th><th>Timeline</th><th>Progress</th><th>Status</th><th>Actions</th></tr></thead><tbody>
 {mktProposals.map(p => (
 <tr key={p.id}>
 <td>{p.title}</td><td>{(p.proposal_type||'').toUpperCase()}</td><td>{p.marketer_name||'-'}</td>
 <td>&#8358;{Number(p.budget_estimate||0).toLocaleString()}</td>
 <td>{p.timeline_start} - {p.timeline_end}</td>
 <td><div style={{background:'#e9ecef',borderRadius:8,overflow:'hidden',height:20,minWidth:80}}><div style={{background: p.execution_progress>=100?'#28a745':p.execution_progress>=50?'#007bff':'#ffc107',height:'100%',width:`${p.execution_progress||0}%`,display:'flex',alignItems:'center',justifyContent:'center',color:'#fff',fontSize:11,fontWeight:600}}>{p.execution_progress||0}%</div></div></td>
 <td><span style={{padding:'2px 8px',borderRadius:12,fontSize:12,fontWeight:600,background: p.status==='approved'?'#d4edda':p.status==='in_progress'?'#cce5ff':p.status==='completed'?'#d1ecf1':p.status==='rejected'?'#f8d7da':'#fff3cd',color: p.status==='approved'?'#155724':p.status==='in_progress'?'#004085':p.status==='completed'?'#0c5460':p.status==='rejected'?'#721c24':'#856404'}}>{(p.status||'draft').replace('_',' ').toUpperCase()}</span></td>
 <td>
 <button className="btn btn-sm btn-secondary" style={{marginRight:4}} onClick={() => { setEditingMktProposal(p); setMktProposalForm({marketer_staff_id:p.marketer_staff_id,title:p.title,proposal_type:p.proposal_type||'campaign',target_audience:p.target_audience||'',description:p.description||'',strategy:p.strategy||'',expected_outcome:p.expected_outcome||'',budget_estimate:p.budget_estimate||'',timeline_start:p.timeline_start||'',timeline_end:p.timeline_end||'',products_involved:p.products_involved||'',channels:p.channels||'',kpi_metrics:p.kpi_metrics||'',status:p.status||'draft'}); }}>Edit</button>
 <button className="btn btn-sm btn-danger" onClick={() => deleteMktProposal(p.id)}>Delete</button>
 </td>
 </tr>
 ))}
 {mktProposals.length === 0 && <tr><td colSpan={8} style={{textAlign:'center',padding:20}}>No proposals found</td></tr>}
 </tbody></table></div>
 </div>
 )}

 {/* Products Catalog View (Read-Only) */}
 {mktView === 'products' && (
 <div>
 <div style={{background:'#fff3cd',borderRadius:8,padding:12,marginBottom:16,color:'#856404'}}>
 <strong>Read-Only:</strong> Product prices are view-only. Contact management to request price changes.
 </div>
 <div className="table-responsive"><table className="data-table"><thead><tr><th>Product Name</th><th>Manufacturer</th><th>Unit</th><th>Pricing</th></tr></thead><tbody>
 {mktProductsCatalog.map(p => (
 <tr key={p.id}>
 <td><strong>{p.product_name}</strong><br/><small style={{color:'#888'}}>{p.sku}</small></td>
 <td>{p.category||'-'}</td>
 <td>{p.unit_of_measure||'-'}</td>
 <td>{p.pricing && p.pricing.length > 0 ? p.pricing.map((pr,i) => (
 <div key={i} style={{marginBottom:4}}><strong>{pr.unit}:</strong> Retail &#8358;{Number(pr.retail_price||0).toLocaleString()} | Wholesale &#8358;{Number(pr.wholesale_price||0).toLocaleString()}</div>
 )) : <span>&#8358;{Number(p.selling_price||0).toLocaleString()}</span>}</td>
 </tr>
 ))}
 {mktProductsCatalog.length === 0 && <tr><td colSpan={4} style={{textAlign:'center',padding:20}}>No products found</td></tr>}
 </tbody></table></div>
 </div>
 )}

 {/* ============ BEST-PRACTICE GUIDE ============ */}
 {mktView === 'guide' && (
 <div style={{maxWidth:1100}}>
 <div style={{background:'linear-gradient(135deg,#4f46e5,#7c3aed)',color:'#fff',borderRadius:10,padding:'18px 22px',marginBottom:18}}>
 <h3 style={{margin:0}}>Pharmaceutical Field Marketing Playbook</h3>
 <p style={{margin:'6px 0 0',opacity:0.95}}>The standard of practice for every Astro-Asix medical sales representative. Read it. Live it. Document it.</p>
 </div>

 <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(320px,1fr))',gap:16}}>

 <div style={{background:'#fff',border:'1px solid #e5e7eb',borderRadius:10,padding:18}}>
 <h4 style={{marginTop:0,color:'#1e293b'}}>1. Pre-Call Planning (the 80/20 rule)</h4>
 <ul style={{margin:0,paddingLeft:18,lineHeight:1.7,color:'#334155'}}>
 <li>Review the facility profile, last visit notes, prescribing pattern and any pending follow-up before leaving the office.</li>
 <li>Set <strong>SMART</strong> call objectives: Specific, Measurable, Achievable, Relevant, Time-bound (e.g., "Get Dr. X to add Wound Clex to formulary review by month-end").</li>
 <li>Carry: detail aid, samples (signed-out), price list, order pad, business card, branded gifts within compliance limits.</li>
 <li>Confirm appointment via SMS / WhatsApp 24h prior. Respect HCP time.</li>
 </ul>
 </div>

 <div style={{background:'#fff',border:'1px solid #e5e7eb',borderRadius:10,padding:18}}>
 <h4 style={{marginTop:0,color:'#1e293b'}}>2. The Detail Call  AIDCA Model</h4>
 <ul style={{margin:0,paddingLeft:18,lineHeight:1.7,color:'#334155'}}>
 <li><strong>Attention:</strong> Open with a clinically relevant insight, recent guideline, or a question.</li>
 <li><strong>Interest:</strong> Probe the HCP's patient profile and unmet need.</li>
 <li><strong>Desire:</strong> Present the feature  benefit  proof (study, KOL endorsement, real-world evidence).</li>
 <li><strong>Conviction:</strong> Handle objections with empathy + evidence. Never argue.</li>
 <li><strong>Action:</strong> Ask for the commitment ("Will you prescribe to your next 3 eligible patients?"). No close, no sale.</li>
 </ul>
 </div>

 <div style={{background:'#fff',border:'1px solid #e5e7eb',borderRadius:10,padding:18}}>
 <h4 style={{marginTop:0,color:'#1e293b'}}>3. Sample &amp; Promotional Material Handling</h4>
 <ul style={{margin:0,paddingLeft:18,lineHeight:1.7,color:'#334155'}}>
 <li>Samples are <strong>not stock for sale</strong>. Every unit must be signed-out, signed-for, and reconciled monthly.</li>
 <li>Distribute only to licensed practitioners. Record name, license #, qty, date in the Daily Log.</li>
 <li>Store away from sunlight, never in vehicle for &gt;48h. Withdraw any stock within 90 days of expiry.</li>
 <li>Promotional materials must carry approved version codes. Discard outdated versions immediately.</li>
 </ul>
 </div>

 <div style={{background:'#fff',border:'1px solid #e5e7eb',borderRadius:10,padding:18}}>
 <h4 style={{marginTop:0,color:'#1e293b'}}>4. Territory &amp; Account Management</h4>
 <ul style={{margin:0,paddingLeft:18,lineHeight:1.7,color:'#334155'}}>
 <li>Segment your facilities into A/B/C accounts by potential value, not current sales.</li>
 <li><strong>A-accounts:</strong> visit weekly. <strong>B:</strong> bi-weekly. <strong>C:</strong> monthly. <strong>Prospects:</strong> qualify within 30 days.</li>
 <li>Map decision chain: Prescriber  Pharmacist  Procurement  Finance  MD. Build relationship at every layer.</li>
 <li>Maintain a 90-day rolling territory plan; review with line manager every Friday.</li>
 </ul>
 </div>

 <div style={{background:'#fff',border:'1px solid #e5e7eb',borderRadius:10,padding:18}}>
 <h4 style={{marginTop:0,color:'#1e293b'}}>5. Documentation Standards</h4>
 <ul style={{margin:0,paddingLeft:18,lineHeight:1.7,color:'#334155'}}>
 <li>Submit <strong>Weekly Plan by Friday 5pm</strong> for the following week. No plan = no field movement.</li>
 <li>Submit <strong>Daily Log within 24h</strong> of every call. Include outcome, samples given, orders placed, follow-up date.</li>
 <li>Update facility &amp; contact records the same day as new info is gathered.</li>
 <li>All entries are auditable. False reporting is a terminable offence.</li>
 </ul>
 </div>

 <div style={{background:'#fff',border:'1px solid #e5e7eb',borderRadius:10,padding:18}}>
 <h4 style={{marginTop:0,color:'#1e293b'}}>6. Compliance &amp; Ethics (Non-negotiable)</h4>
 <ul style={{margin:0,paddingLeft:18,lineHeight:1.7,color:'#334155'}}>
 <li>Comply with NAFDAC, PCN, and PMG-MAN code of marketing practice at all times.</li>
 <li>No off-label promotion. Stick strictly to the approved indications and the SmPC.</li>
 <li>Gifts &amp; hospitality: educational only, modest value, never tied to prescribing decisions.</li>
 <li>Adverse event reports: capture and forward to PV within 24h  no exceptions.</li>
 <li>Patient data confidentiality is sacred. Never photograph or transcribe patient information.</li>
 </ul>
 </div>

 <div style={{background:'#fff',border:'1px solid #e5e7eb',borderRadius:10,padding:18}}>
 <h4 style={{marginTop:0,color:'#1e293b'}}>7. KPIs You Will Be Measured On</h4>
 <ul style={{margin:0,paddingLeft:18,lineHeight:1.7,color:'#334155'}}>
 <li>Call frequency vs plan (target  95%).</li>
 <li>Order conversion rate (orders  qualified calls).</li>
 <li>New customers acquired per month.</li>
 <li>Facility coverage breadth and depth.</li>
 <li>Sample-to-sale ratio.</li>
 <li>Follow-up completion within SLA.</li>
 <li>Revenue achieved vs target.</li>
 </ul>
 </div>

 <div style={{background:'#fff',border:'1px solid #e5e7eb',borderRadius:10,padding:18}}>
 <h4 style={{marginTop:0,color:'#1e293b'}}>8. Weekly Operating Rhythm</h4>
 <ul style={{margin:0,paddingLeft:18,lineHeight:1.7,color:'#334155'}}>
 <li><strong>Mon 7:30am:</strong> regional huddle, KPI review, week-plan confirmation.</li>
 <li><strong>TueThu:</strong> field execution; daily log filed by 8pm.</li>
 <li><strong>Fri AM:</strong> high-value account closing visits, collections, sample reconciliation.</li>
 <li><strong>Fri PM:</strong> next-week plan submission, expense report, CRM clean-up.</li>
 <li><strong>Monthly:</strong> business review with manager  wins, losses, gaps, action plan.</li>
 </ul>
 </div>
 </div>
 </div>
 )}

 {/* ============ RESPONSIBILITIES ============ */}
 {mktView === 'responsibilities' && (
 <div style={{maxWidth:1000}}>
 <div style={{background:'#0f172a',color:'#fff',borderRadius:10,padding:'18px 22px',marginBottom:18}}>
 <h3 style={{margin:0}}>Marketer / Medical Sales Representative  Job Responsibilities</h3>
 <p style={{margin:'6px 0 0',opacity:0.9}}>Every responsibility below is tracked in this module. If it isn't logged, it didn't happen.</p>
 </div>

 <ol style={{lineHeight:1.8,color:'#1e293b',fontSize:15,paddingLeft:22}}>
 <li><strong>Territory Planning &amp; Coverage</strong>  Build, maintain and execute a written weekly plan that covers all A/B/C facilities at their assigned frequency. Submit through the <em>Weekly Plans</em> tab.</li>
 <li><strong>Healthcare Facility Database</strong>  Maintain an accurate, up-to-date list of every hospital, clinic, pharmacy, lab and dispensary in the territory, with full decision-maker contacts. Update through the <em>Facilities &amp; Contacts</em> tab.</li>
 <li><strong>Customer Acquisition</strong>  Identify, qualify, onboard and grow new healthcare accounts. All new customers must be created through the Customers module so attribution is captured.</li>
 <li><strong>Detailing &amp; Product Promotion</strong>  Deliver compliant, evidence-based detail calls to HCPs on the approved product range using current detail aids only.</li>
 <li><strong>Sample Management</strong>  Collect, distribute and reconcile samples per SOP. Record every sample issue in the Daily Log.</li>
 <li><strong>Order Generation &amp; Collections</strong>  Take orders, raise quotes, expedite delivery, follow up on receivables, and chase outstanding payments to within agreed credit terms.</li>
 <li><strong>Daily Reporting</strong>  File a Daily Log within 24 hours of every working day, including all calls made, outcomes, samples, orders and follow-up actions.</li>
 <li><strong>Follow-Ups</strong>  Honour every committed follow-up date. Pending follow-ups are flagged on your dashboard.</li>
 <li><strong>Market Intelligence</strong>  Capture competitor activity, pricing moves, KOL opinions, formulary changes and patient flow data; share weekly.</li>
 <li><strong>Marketing Proposals</strong>  Initiate and execute approved campaigns, CME meetings, hospital launches, and KOL engagements through the <em>Proposals</em> tab.</li>
 <li><strong>Brand &amp; Compliance</strong>  Represent the company's reputation. Comply with NAFDAC, PCN, GDP and internal SOPs at all times. Report adverse events within 24h.</li>
 <li><strong>Continuous Learning</strong>  Stay current on the SmPC, clinical guidelines, and competitor literature. Attend all training and certification programmes.</li>
 <li><strong>Performance Accountability</strong>  Achieve assigned monthly KPIs (calls, conversion, new customers, revenue). Performance is reviewed in the <em>Performance &amp; Sales</em> tab.</li>
 <li><strong>Customer Care Hand-off</strong>  Ensure smooth onboarding to the Customer Care team after first order so retention is owned end-to-end.</li>
 <li><strong>Team Collaboration</strong>  Work with Logistics, Production, Finance and HR to ensure the customer experience is seamless.</li>
 </ol>
 </div>
 )}

 {/* ============ FACILITIES & CONTACTS ============ */}
 {mktView === 'facilities' && (
 <div>
 <div style={{background:'#ecfdf5',border:'1px solid #a7f3d0',color:'#065f46',borderRadius:8,padding:12,marginBottom:16}}>
 <strong>Healthcare Facility CRM:</strong> Every facility you visit and every contact person you engage must be captured here. This is the single source of truth for territory coverage and account ownership.
 </div>

 <form onSubmit={saveMktFacility} style={{background:'#f8fafc',border:'1px solid #e2e8f0',borderRadius:10,padding:16,marginBottom:20}}>
 <h3 style={{marginTop:0}}>{editingMktFacility ? 'Edit Facility' : 'Add New Facility'}</h3>
 <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(220px,1fr))',gap:12}}>
 <div className="form-group"><label>Facility Name *</label><input value={mktFacilityForm.facility_name} onChange={e=>setMktFacilityForm({...mktFacilityForm,facility_name:e.target.value})} required/></div>
 <div className="form-group"><label>Type</label>
 <select value={mktFacilityForm.facility_type} onChange={e=>setMktFacilityForm({...mktFacilityForm,facility_type:e.target.value})}>
 <option value="hospital">Hospital</option>
 <option value="clinic">Clinic</option>
 <option value="pharmacy">Pharmacy</option>
 <option value="lab">Lab / Diagnostics</option>
 <option value="dispensary">Dispensary</option>
 <option value="distributor">Distributor</option>
 <option value="other">Other</option>
 </select>
 </div>
 <div className="form-group"><label>Assigned Marketer</label>
 <select value={mktFacilityForm.marketer_staff_id} onChange={e=>setMktFacilityForm({...mktFacilityForm,marketer_staff_id:e.target.value})}>
 <option value=""> Select </option>
 {(data.staff||[]).map(s => <option key={s.id} value={s.id}>{s.first_name} {s.last_name}</option>)}
 </select>
 </div>
 <div className="form-group"><label>Relationship Status</label>
 <select value={mktFacilityForm.relationship_status} onChange={e=>setMktFacilityForm({...mktFacilityForm,relationship_status:e.target.value})}>
 <option value="prospect">Prospect</option>
 <option value="qualified">Qualified Lead</option>
 <option value="active">Active Customer</option>
 <option value="key_account">Key Account</option>
 <option value="dormant">Dormant</option>
 <option value="lost">Lost</option>
 </select>
 </div>
 <div className="form-group"><label>Address</label><input value={mktFacilityForm.address} onChange={e=>setMktFacilityForm({...mktFacilityForm,address:e.target.value})}/></div>
 <div className="form-group"><label>City</label><input value={mktFacilityForm.city} onChange={e=>setMktFacilityForm({...mktFacilityForm,city:e.target.value})}/></div>
 <div className="form-group"><label>State</label><input value={mktFacilityForm.state} onChange={e=>setMktFacilityForm({...mktFacilityForm,state:e.target.value})}/></div>
 <div className="form-group"><label>Primary Contact Person</label><input value={mktFacilityForm.contact_person} onChange={e=>setMktFacilityForm({...mktFacilityForm,contact_person:e.target.value})}/></div>
 <div className="form-group"><label>Title / Role</label><input placeholder="e.g. Chief Pharmacist" value={mktFacilityForm.contact_title} onChange={e=>setMktFacilityForm({...mktFacilityForm,contact_title:e.target.value})}/></div>
 <div className="form-group"><label>Phone</label><input value={mktFacilityForm.contact_phone} onChange={e=>setMktFacilityForm({...mktFacilityForm,contact_phone:e.target.value})}/></div>
 <div className="form-group"><label>Email</label><input type="email" value={mktFacilityForm.contact_email} onChange={e=>setMktFacilityForm({...mktFacilityForm,contact_email:e.target.value})}/></div>
 <div className="form-group"><label>Secondary Contact</label><input value={mktFacilityForm.secondary_contact} onChange={e=>setMktFacilityForm({...mktFacilityForm,secondary_contact:e.target.value})}/></div>
 <div className="form-group"><label>Secondary Phone</label><input value={mktFacilityForm.secondary_phone} onChange={e=>setMktFacilityForm({...mktFacilityForm,secondary_phone:e.target.value})}/></div>
 <div className="form-group"><label>Last Visit</label><input type="date" value={mktFacilityForm.last_visit_date} onChange={e=>setMktFacilityForm({...mktFacilityForm,last_visit_date:e.target.value})}/></div>
 <div className="form-group"><label>Next Visit</label><input type="date" value={mktFacilityForm.next_visit_date} onChange={e=>setMktFacilityForm({...mktFacilityForm,next_visit_date:e.target.value})}/></div>
 <div className="form-group"><label>Visit Frequency</label>
 <select value={mktFacilityForm.visit_frequency} onChange={e=>setMktFacilityForm({...mktFacilityForm,visit_frequency:e.target.value})}>
 <option value="weekly">Weekly</option>
 <option value="biweekly">Bi-weekly</option>
 <option value="monthly">Monthly</option>
 <option value="quarterly">Quarterly</option>
 </select>
 </div>
 <div className="form-group"><label>Estimated Monthly Value (&#8358;)</label><input type="number" min="0" value={mktFacilityForm.estimated_monthly_value} onChange={e=>setMktFacilityForm({...mktFacilityForm,estimated_monthly_value:e.target.value})}/></div>
 <div className="form-group" style={{gridColumn:'1 / -1'}}><label>Products of Interest</label><input value={mktFacilityForm.products_of_interest} onChange={e=>setMktFacilityForm({...mktFacilityForm,products_of_interest:e.target.value})}/></div>
 <div className="form-group" style={{gridColumn:'1 / -1'}}><label>Notes</label><textarea rows={2} value={mktFacilityForm.notes} onChange={e=>setMktFacilityForm({...mktFacilityForm,notes:e.target.value})}/></div>
 </div>
 <div style={{marginTop:12}}>
 <button type="submit" className="btn btn-primary">{editingMktFacility ? 'Update Facility' : 'Add Facility'}</button>
 {editingMktFacility && <button type="button" className="btn btn-secondary" style={{marginLeft:8}} onClick={()=>{ setEditingMktFacility(null); setMktFacilityForm({ marketer_staff_id:'', facility_name:'', facility_type:'hospital', address:'', city:'', state:'', contact_person:'', contact_title:'', contact_phone:'', contact_email:'', secondary_contact:'', secondary_phone:'', relationship_status:'prospect', products_of_interest:'', last_visit_date:'', next_visit_date:'', visit_frequency:'monthly', estimated_monthly_value:'0', notes:'' }); }}>Cancel</button>}
 </div>
 </form>

 <div className="table-responsive"><table className="data-table"><thead><tr>
 <th>Facility</th><th>Type</th><th>Location</th><th>Marketer</th><th>Primary Contact</th><th>Phone</th><th>Status</th><th>Last Visit</th><th>Next Visit</th><th>Est. ₦/mo</th><th>Actions</th>
 </tr></thead><tbody>
 {mktFacilities.map(f => (
 <tr key={f.id}>
 <td><strong>{f.facility_name}</strong>{f.products_of_interest && <div style={{fontSize:11,color:'#64748b'}}>Interested in: {f.products_of_interest}</div>}</td>
 <td>{f.facility_type}</td>
 <td>{[f.city,f.state].filter(Boolean).join(', ') || f.address}</td>
 <td>{f.marketer_name||'-'}</td>
 <td>{f.contact_person}{f.contact_title && <div style={{fontSize:11,color:'#64748b'}}>{f.contact_title}</div>}{f.secondary_contact && <div style={{fontSize:11,color:'#64748b'}}>2nd: {f.secondary_contact}</div>}</td>
 <td>{f.contact_phone && <a href={`tel:${f.contact_phone}`}>{f.contact_phone}</a>}{f.contact_email && <div><a href={`mailto:${f.contact_email}`} style={{fontSize:11}}>{f.contact_email}</a></div>}{f.secondary_phone && <div style={{fontSize:11}}><a href={`tel:${f.secondary_phone}`}>{f.secondary_phone}</a></div>}</td>
 <td><span style={{padding:'2px 8px',borderRadius:12,fontSize:11,background:f.relationship_status==='active'||f.relationship_status==='key_account'?'#d1fae5':f.relationship_status==='prospect'?'#fef3c7':'#fee2e2',color:f.relationship_status==='active'||f.relationship_status==='key_account'?'#065f46':f.relationship_status==='prospect'?'#92400e':'#991b1b'}}>{f.relationship_status}</span></td>
 <td>{f.last_visit_date||'-'}</td>
 <td>{f.next_visit_date||'-'}</td>
 <td>&#8358;{Number(f.estimated_monthly_value||0).toLocaleString()}</td>
 <td>
 <button className="btn btn-sm btn-secondary" style={{marginRight:4}} onClick={()=>{ setEditingMktFacility(f); setMktFacilityForm({ marketer_staff_id:f.marketer_staff_id||'', facility_name:f.facility_name||'', facility_type:f.facility_type||'hospital', address:f.address||'', city:f.city||'', state:f.state||'', contact_person:f.contact_person||'', contact_title:f.contact_title||'', contact_phone:f.contact_phone||'', contact_email:f.contact_email||'', secondary_contact:f.secondary_contact||'', secondary_phone:f.secondary_phone||'', relationship_status:f.relationship_status||'prospect', products_of_interest:f.products_of_interest||'', last_visit_date:f.last_visit_date||'', next_visit_date:f.next_visit_date||'', visit_frequency:f.visit_frequency||'monthly', estimated_monthly_value:String(f.estimated_monthly_value||0), notes:f.notes||'' }); }}>Edit</button>
 <button className="btn btn-sm btn-danger" onClick={()=>deleteMktFacility(f.id)}>Delete</button>
 </td>
 </tr>
 ))}
 {mktFacilities.length === 0 && <tr><td colSpan={11} style={{textAlign:'center',padding:20}}>No facilities recorded yet. Add your first facility above.</td></tr>}
 </tbody></table></div>
 </div>
 )}

 {/* ============ PERFORMANCE & SALES ============ */}
 {mktView === 'performance' && (
 <div>
 <div style={{display:'flex',gap:12,alignItems:'flex-end',flexWrap:'wrap',marginBottom:16}}>
 <div className="form-group"><label>Window (days)</label>
 <select value={mktPerfDays} onChange={e=>setMktPerfDays(Number(e.target.value))}>
 <option value={7}>Last 7 days</option>
 <option value={30}>Last 30 days</option>
 <option value={90}>Last 90 days</option>
 <option value={180}>Last 6 months</option>
 <option value={365}>Last 12 months</option>
 </select>
 </div>
 <div className="form-group"><label>Marketer</label>
 <select value={mktPerfMarketer} onChange={e=>setMktPerfMarketer(e.target.value)}>
 <option value="">All marketers</option>
 {(data.staff||[]).map(s => <option key={s.id} value={s.id}>{s.first_name} {s.last_name}</option>)}
 </select>
 </div>
 <button className="btn btn-primary" onClick={fetchMktPerformance}>Refresh</button>
 </div>

 {mktPerformance && (
 <>
 <div className="stats-grid" style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(200px,1fr))',gap:16,marginBottom:24}}>
 <div className="stat-card primary"><h4>Active Marketers</h4><p className="stat-number">{mktPerformance.totals?.marketers_active||0}</p></div>
 <div className="stat-card success"><h4>Total Orders</h4><p className="stat-number">{mktPerformance.totals?.total_orders||0}</p></div>
 <div className="stat-card info"><h4>Total Order Value</h4><p className="stat-number">&#8358;{Number(mktPerformance.totals?.total_order_value||0).toLocaleString()}</p></div>
 <div className="stat-card warning"><h4>New Customers</h4><p className="stat-number">{mktPerformance.totals?.new_customers_count||0}</p></div>
 </div>

 <h3 style={{marginTop:0}}>Per-Marketer Scorecard</h3>
 <div className="table-responsive" style={{marginBottom:24}}><table className="data-table"><thead><tr>
 <th>Marketer</th><th>Logs Filed</th><th>Unique Customers</th><th>Locations</th><th>Orders</th><th>Order Value</th><th>Samples</th><th>Pending Follow-ups</th><th>Avg Mood</th>
 </tr></thead><tbody>
 {(mktPerformance.per_marketer||[]).map((m,i)=>(
 <tr key={i}>
 <td><strong>{m.marketer_name}</strong></td>
 <td>{m.total_logs}</td>
 <td>{m.unique_customers_contacted}</td>
 <td>{m.unique_locations}</td>
 <td>{m.total_orders}</td>
 <td>&#8358;{Number(m.total_order_value).toLocaleString()}</td>
 <td>{m.total_samples}</td>
 <td>{m.pending_followups}</td>
 <td>{m.avg_mood?m.avg_mood.toFixed(1):'-'}</td>
 </tr>
 ))}
 {(mktPerformance.per_marketer||[]).length===0 && <tr><td colSpan={9} style={{textAlign:'center',padding:20}}>No activity in the selected window.</td></tr>}
 </tbody></table></div>

 <h3>New Customers Acquired</h3>
 <div className="table-responsive" style={{marginBottom:24}}><table className="data-table"><thead><tr>
 <th>Customer</th><th>Phone</th><th>Email</th><th>Created</th><th>Attributed Marketer</th>
 </tr></thead><tbody>
 {(mktPerformance.new_customers||[]).map((c,i)=>(
 <tr key={i}>
 <td><strong>{c.name}</strong></td>
 <td>{c.phone||'-'}</td>
 <td>{c.email||'-'}</td>
 <td>{c.created_at?.split('T')[0]}</td>
 <td>{c.attributed_marketer || <em style={{color:'#94a3b8'}}>unattributed</em>}</td>
 </tr>
 ))}
 {(mktPerformance.new_customers||[]).length===0 && <tr><td colSpan={5} style={{textAlign:'center',padding:20}}>No new customers in the selected window.</td></tr>}
 </tbody></table></div>

 <h3>Top Facilities by Sales Contribution</h3>
 <div className="table-responsive"><table className="data-table"><thead><tr>
 <th>Facility</th><th>Type</th><th>City</th><th>Status</th><th>Visits</th><th>Orders</th><th>Order Value</th>
 </tr></thead><tbody>
 {(mktPerformance.top_facilities||[]).map((f,i)=>(
 <tr key={i}>
 <td><strong>{f.facility_name}</strong></td>
 <td>{f.facility_type||'-'}</td>
 <td>{f.city||'-'}</td>
 <td>{f.relationship_status}</td>
 <td>{f.visit_count}</td>
 <td>{f.orders}</td>
 <td>&#8358;{Number(f.value).toLocaleString()}</td>
 </tr>
 ))}
 {(mktPerformance.top_facilities||[]).length===0 && <tr><td colSpan={7} style={{textAlign:'center',padding:20}}>No facilities tracked yet  add some in the Facilities &amp; Contacts tab.</td></tr>}
 </tbody></table></div>
 </>
 )}
 </div>
 )}
 </div>
 )}

 {/* ==================== HR / CUSTOMER CARE MODULE ==================== */}
 {activeModule === 'hrCustomerCare' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>HR / Customer Care</h2>
 </div>
 <div className="module-actions">
 {['dashboard','staff','performance','attendance','products','sales','customers'].map(v => (
 <button key={v} className={`btn ${hrView===v?'btn-primary':'btn-secondary'}`} onClick={() => setHrView(v)} style={{marginRight:6}}>
 {v === 'staff' ? 'All Staff' : v === 'performance' ? 'Performance' : v === 'attendance' ? 'Attendance' : v === 'products' ? 'Products' : v === 'sales' ? 'Sales Orders' : v === 'customers' ? 'Customers' : 'Dashboard'}
 </button>
 ))}
 </div>
 </div>

 {/* HR Dashboard View */}
 {hrView === 'dashboard' && hrDashboard && (
 <div>
 <div className="stats-grid" style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(200px,1fr))',gap:16,marginBottom:24}}>
 <div className="stat-card primary"><h4>Total Staff</h4><p className="stat-number">{hrDashboard.total_staff||0}</p></div>
 <div className="stat-card success"><h4>Active Today</h4><p className="stat-number">{hrDashboard.active_today||0}</p></div>
 <div className="stat-card info"><h4>Total Products</h4><p className="stat-number">{hrDashboard.total_products||0}</p></div>
 <div className="stat-card warning"><h4>Total Customers</h4><p className="stat-number">{hrDashboard.total_customers||0}</p></div>
 <div className="stat-card success"><h4>Monthly Orders</h4><p className="stat-number">{hrDashboard.month_orders||0}</p></div>
 <div className="stat-card primary"><h4>Monthly Revenue</h4><p className="stat-number">&#8358;{Number(hrDashboard.month_revenue||0).toLocaleString()}</p></div>
 <div className="stat-card danger"><h4>Pending Orders</h4><p className="stat-number">{hrDashboard.pending_orders||0}</p></div>
 </div>
 {hrDashboard.upcoming_birthdays && hrDashboard.upcoming_birthdays.length > 0 && (
 <div style={{background:'#fff3cd',borderRadius:8,padding:16,marginBottom:24}}>
 <h3 style={{marginBottom:12,color:'#856404'}}>Upcoming Birthdays ({hrDashboard.upcoming_birthdays.length} staff)</h3>
 <div style={{display:'flex',flexWrap:'wrap',gap:12}}>
 {hrDashboard.upcoming_birthdays.map((b,i) => (
 <div key={i} style={{background:b.is_today?'linear-gradient(135deg,#ff6b6b,#ee5a24)':'#fff',borderRadius:8,padding:'8px 16px',boxShadow:'0 1px 3px rgba(0,0,0,.1)',color:b.is_today?'#fff':'#333'}}>
 <strong>{b.first_name} {b.last_name}</strong><br /><small>{b.is_today ? 'Birthday Today!' : `in ${b.days_until} day${b.days_until>1?'s':''}`} - {b.position||'Staff'}</small>
 </div>
 ))}
 </div>
 </div>
 )}
 </div>
 )}

 {/* Staff List View */}
 {hrView === 'staff' && (
 <div>
 <div style={{background:'#d1ecf1',borderRadius:8,padding:12,marginBottom:16,color:'#0c5460'}}>
 <strong>HR View:</strong> Staff records are read-only. Contact admin for modifications.
 </div>
 <div className="table-responsive"><table className="data-table"><thead><tr><th>Employee ID</th><th>Name</th><th>Position</th><th>Phone</th><th>Hourly Rate</th><th>Bank</th><th>Account</th></tr></thead><tbody>
 {hrStaffList.map(s => (
 <tr key={s.id}>
 <td><strong>{s.employee_id}</strong></td><td>{s.first_name} {s.last_name}</td><td>{s.position||'-'}</td>
 <td>{s.phone||'-'}</td><td>&#8358;{Number(s.hourly_rate||0).toLocaleString()}</td>
 <td>{s.bank_name||'-'}</td><td>{s.account_number||'-'}</td>
 </tr>
 ))}
 {hrStaffList.length === 0 && <tr><td colSpan={7} style={{textAlign:'center',padding:20}}>No staff records</td></tr>}
 </tbody></table></div>
 </div>
 )}

 {/* Performance View */}
 {hrView === 'performance' && (
 <div>
 <div style={{display:'flex',alignItems:'center',gap:12,marginBottom:16}}>
 <label style={{fontWeight:600}}>Period (days):</label>
 <select value={hrPerfDays} onChange={e => { setHrPerfDays(Number(e.target.value)); fetchHrPerformance(Number(e.target.value)); }} style={{padding:'6px 12px',borderRadius:6,border:'1px solid #ddd'}}>
 <option value={7}>Last 7 days</option><option value={14}>Last 14 days</option><option value={30}>Last 30 days</option><option value={60}>Last 60 days</option><option value={90}>Last 90 days</option>
 </select>
 </div>
 <div className="table-responsive"><table className="data-table"><thead><tr><th>#</th><th>Employee</th><th>Position</th><th>Days Present</th><th>Total Hours</th><th>Avg Hours/Day</th><th>Production Tasks</th><th>Rating</th></tr></thead><tbody>
 {hrPerformance.map((p,i) => (
 <tr key={p.staff_id}>
 <td>{i+1}</td><td><strong>{p.employee_id}</strong> - {p.first_name} {p.last_name}</td><td>{p.position||'-'}</td>
 <td>{p.days_present||0}</td><td>{Number(p.total_hours||0).toFixed(1)}</td><td>{Number(p.avg_hours_per_day||0).toFixed(1)}</td>
 <td>{p.production_tasks||0}</td>
 <td><span style={{padding:'2px 10px',borderRadius:12,fontSize:12,fontWeight:700,background: (p.avg_hours_per_day||0)>=7?'#d4edda':(p.avg_hours_per_day||0)>=5?'#fff3cd':'#f8d7da',color: (p.avg_hours_per_day||0)>=7?'#155724':(p.avg_hours_per_day||0)>=5?'#856404':'#721c24'}}>{(p.avg_hours_per_day||0)>=7?'Excellent':(p.avg_hours_per_day||0)>=5?'Good':'Needs Improvement'}</span></td>
 </tr>
 ))}
 {hrPerformance.length === 0 && <tr><td colSpan={8} style={{textAlign:'center',padding:20}}>No performance data for this period</td></tr>}
 </tbody></table></div>
 </div>
 )}

 {/* Attendance Log View */}
 {hrView === 'attendance' && (
 <div>
 <h3 style={{marginBottom:16}}>Recent Attendance Log (Last 7 Days)</h3>
 <div className="table-responsive"><table className="data-table"><thead><tr><th>Date</th><th>Employee</th><th>Clock In</th><th>Clock Out</th><th>Hours</th></tr></thead><tbody>
 {hrAttendanceLog.map((a,i) => (
 <tr key={i}>
 <td>{a.date||'-'}</td><td><strong>{a.employee_id}</strong> - {a.first_name} {a.last_name}</td>
 <td>{a.clock_in ? new Date(a.clock_in).toLocaleTimeString() : '-'}</td>
 <td>{a.clock_out ? new Date(a.clock_out).toLocaleTimeString() : '-'}</td>
 <td>{a.hours_worked ? Number(a.hours_worked).toFixed(1) : '-'}</td>
 </tr>
 ))}
 {hrAttendanceLog.length === 0 && <tr><td colSpan={5} style={{textAlign:'center',padding:20}}>No attendance records</td></tr>}
 </tbody></table></div>
 </div>
 )}

 {/* Products Catalog View (Read-Only) */}
 {hrView === 'products' && (
 <div>
 <div style={{background:'#fff3cd',borderRadius:8,padding:12,marginBottom:16,color:'#856404'}}>
 <strong>Read-Only:</strong> Product prices are view-only. Contact admin to request changes.
 </div>
 <div className="table-responsive"><table className="data-table"><thead><tr><th>Product Name</th><th>Manufacturer</th><th>Cost Price</th><th>Pricing Details</th></tr></thead><tbody>
 {hrProductsCatalog.map(p => (
 <tr key={p.id}>
 <td><strong>{p.name}</strong><br/><small style={{color:'#888'}}>{p.sku}</small></td>
 <td>{p.category||'-'}</td>
 <td>&#8358;{Number(p.cost_price||0).toLocaleString()}</td>
 <td>{p.pricing && p.pricing.length > 0 ? p.pricing.map((pr,i) => (
 <div key={i} style={{marginBottom:4}}><strong>{pr.unit}:</strong> Cost &#8358;{Number(pr.cost_price||0).toLocaleString()} | Retail &#8358;{Number(pr.retail_price||0).toLocaleString()} | Wholesale &#8358;{Number(pr.wholesale_price||0).toLocaleString()}</div>
 )) : <span>Retail &#8358;{Number(p.selling_price||0).toLocaleString()} | Wholesale &#8358;{Number(p.wholesale_price||0).toLocaleString()}</span>}</td>
 </tr>
 ))}
 {hrProductsCatalog.length === 0 && <tr><td colSpan={4} style={{textAlign:'center',padding:20}}>No products found</td></tr>}
 </tbody></table></div>
 </div>
 )}

 {/* Sales Orders View */}
 {hrView === 'sales' && (
 <div>
 <h3 style={{marginBottom:16}}>Sales Orders</h3>
 <div className="table-responsive"><table className="data-table"><thead><tr><th>Order ID</th><th>Customer</th><th>Date</th><th>Total</th><th>Status</th><th>Items</th></tr></thead><tbody>
 {hrSalesOrders.map(o => (
 <tr key={o.id}>
 <td><strong>{(o.id||'').substring(0,8)}...</strong></td>
 <td>{o.customer_name||'-'}</td>
 <td>{o.order_date||'-'}</td>
 <td>&#8358;{Number(o.total_amount||0).toLocaleString()}</td>
 <td><span style={{padding:'2px 8px',borderRadius:12,fontSize:12,fontWeight:600,background: o.status==='completed'?'#d4edda':o.status==='pending'?'#fff3cd':o.status==='cancelled'?'#f8d7da':'#cce5ff',color: o.status==='completed'?'#155724':o.status==='pending'?'#856404':o.status==='cancelled'?'#721c24':'#004085'}}>{(o.status||'pending').toUpperCase()}</span></td>
 <td>{o.item_count||0}</td>
 </tr>
 ))}
 {hrSalesOrders.length === 0 && <tr><td colSpan={6} style={{textAlign:'center',padding:20}}>No sales orders found</td></tr>}
 </tbody></table></div>
 </div>
 )}

 {/* Customers View */}
 {hrView === 'customers' && (
 <div>
 <h3 style={{marginBottom:16}}>Customers</h3>
 <div className="table-responsive"><table className="data-table"><thead><tr><th>Name</th><th>Email</th><th>Phone</th><th>Address</th><th>Total Orders</th></tr></thead><tbody>
 {hrCustomers.map(c => (
 <tr key={c.id}>
 <td>{c.name||c.customer_name||'-'}</td><td>{c.email||'-'}</td><td>{c.phone||'-'}</td>
 <td>{c.address||'-'}</td><td>{c.total_orders||0}</td>
 </tr>
 ))}
 {hrCustomers.length === 0 && <tr><td colSpan={5} style={{textAlign:'center',padding:20}}>No customers found</td></tr>}
 </tbody></table></div>
 </div>
 )}
 </div>
 )}

 {/* ===================== PAYMENT TRACKING MODULE ===================== */}
 {activeModule === 'paymentTracking' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Payments & Debt Reconciliation</h2>
 </div>
 <div className="module-actions">
 {[{k:'dashboard',l:'Dashboard'},{k:'invoices',l:'Invoices'},{k:'debtors',l:'Debtors'},{k:'legacyDebts',l:'Previous Debts'},{k:'reminders',l:'Overdue'}].map(v => (
 <button key={v.k} className={`btn ${ptView===v.k?'btn-primary':'btn-secondary'}`} onClick={() => setPtView(v.k)} style={{marginRight:6}}>
 {v.l}
 </button>
 ))}
 </div>
 </div>

 {/* RECONCILIATION DASHBOARD */}
 {ptView === 'dashboard' && (
 <div>
 {ptReconciliation && (
 <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(200px,1fr))',gap:16,marginBottom:24}}>
 <div style={{background:'#fff',padding:20,borderRadius:8,boxShadow:'0 1px 3px rgba(0,0,0,.1)',textAlign:'center'}}>
 <div style={{fontSize:28,fontWeight:700,color:'#2c3e50'}}>{ptReconciliation.total_invoices || 0}</div>
 <div style={{color:'#888',fontSize:13}}>Total Invoices</div>
 </div>
 <div style={{background:'#fff',padding:20,borderRadius:8,boxShadow:'0 1px 3px rgba(0,0,0,.1)',textAlign:'center'}}>
 <div style={{fontSize:28,fontWeight:700,color:'#3498db'}}>{formatCurrency(ptReconciliation.total_invoiced || 0)}</div>
 <div style={{color:'#888',fontSize:13}}>Total Invoiced</div>
 </div>
 <div style={{background:'#fff',padding:20,borderRadius:8,boxShadow:'0 1px 3px rgba(0,0,0,.1)',textAlign:'center'}}>
 <div style={{fontSize:28,fontWeight:700,color:'#27ae60'}}>{formatCurrency(ptReconciliation.total_paid || 0)}</div>
 <div style={{color:'#888',fontSize:13}}>Total Collected</div>
 </div>
 <div style={{background:'#fff',padding:20,borderRadius:8,boxShadow:'0 1px 3px rgba(0,0,0,.1)',textAlign:'center'}}>
 <div style={{fontSize:28,fontWeight:700,color:'#e74c3c'}}>{formatCurrency(ptReconciliation.total_outstanding || 0)}</div>
 <div style={{color:'#888',fontSize:13}}>Outstanding</div>
 </div>
 <div style={{background:'#fff',padding:20,borderRadius:8,boxShadow:'0 1px 3px rgba(0,0,0,.1)',textAlign:'center'}}>
 <div style={{fontSize:28,fontWeight:700,color:'#f39c12'}}>{(ptReconciliation.collection_rate || 0).toFixed(1)}%</div>
 <div style={{color:'#888',fontSize:13}}>Collection Rate</div>
 </div>
 </div>
 )}

 <h3 style={{marginBottom:12}}>Top Debtors</h3>
 {ptDebtors.length === 0 ? (
 <div style={{background:'#d4edda',padding:20,borderRadius:8,textAlign:'center',color:'#155724'}}>No outstanding debts! All payments are up to date.</div>
 ) : (
 <div className="table-responsive"><table className="data-table"><thead><tr>
 <th>Customer</th><th>Phone</th><th>Invoices</th><th>Total Owed</th><th>Total Paid</th><th>Balance</th><th>Overdue</th><th>Actions</th>
 </tr></thead><tbody>
 {ptDebtors.map(d => (
 <tr key={d.customer_id} style={{background: d.is_overdue ? '#fff5f5' : '#fff'}}>
 <td><strong>{d.customer_name}</strong></td>
 <td>{d.phone || '-'}</td>
 <td>{d.invoice_count}</td>
 <td>{formatCurrency(d.total_invoiced)}</td>
 <td style={{color:'#27ae60'}}>{formatCurrency(d.total_paid)}</td>
 <td style={{color:'#e74c3c',fontWeight:700}}>{formatCurrency(d.balance)}</td>
 <td>{d.is_overdue ? <span style={{color:'#e74c3c',fontWeight:600}}>{d.days_overdue}d overdue</span> : '-'}</td>
 <td>
 <button className="btn btn-sm btn-primary" onClick={() => { setPtView('debtorDetail'); fetchPtDebtorDetail(d.customer_id); }} style={{marginRight:4}}>Details</button>
 <button className="btn btn-sm btn-secondary" onClick={() => { fetchPtReminderMsg(d.customer_id); setPtView('reminder'); }} style={{background:'#25D366',color:'#fff',border:'none'}}>WhatsApp</button>
 </td>
 </tr>
 ))}
 </tbody></table></div>
 )}
 </div>
 )}

 {/* INVOICES LIST */}
 {ptView === 'invoices' && (
 <div>
 <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16}}>
 <h3 style={{margin:0}}>All Invoices ({ptInvoices.length})</h3>
 </div>
 {ptInvoices.length === 0 ? (
 <div style={{background:'#f8f9fa',padding:30,borderRadius:8,textAlign:'center',color:'#666'}}>
 <p>No invoices yet. Generate invoices from the Sales module.</p>
 <button className="btn btn-primary" onClick={() => setActiveModule('sales')}>Go to Sales</button>
 </div>
 ) : (
 <div className="table-responsive"><table className="data-table"><thead><tr>
 <th>Invoice #</th><th>Customer</th><th>Order #</th><th>Date</th><th>Due Date</th><th>Total</th><th>Paid</th><th>Balance</th><th>Status</th><th>Actions</th>
 </tr></thead><tbody>
 {ptInvoices.map(inv => {
 const statusColors = { paid: '#27ae60', partial: '#f39c12', pending: '#3498db', overdue: '#e74c3c' };
 const displayStatus = inv.is_overdue && inv.status !== 'paid' ? 'overdue' : inv.status;
 return (
 <tr key={inv.id} style={{background: displayStatus === 'overdue' ? '#fff5f5' : '#fff'}}>
 <td><strong>{inv.invoice_number}</strong></td>
 <td>{inv.customer_name || '-'}</td>
 <td>{inv.order_number || '-'}</td>
 <td>{inv.invoice_date}</td>
 <td>{inv.due_date}</td>
 <td>{formatCurrency(inv.total_amount)}</td>
 <td style={{color:'#27ae60'}}>{formatCurrency(inv.total_paid || 0)}</td>
 <td style={{color:'#e74c3c',fontWeight:700}}>{formatCurrency(inv.balance || 0)}</td>
 <td><span style={{background: statusColors[displayStatus]||'#888', color:'#fff', padding:'3px 10px', borderRadius:12, fontSize:11, fontWeight:600, textTransform:'uppercase'}}>{displayStatus}</span></td>
 <td>
 <button className="btn btn-sm btn-primary" onClick={() => { setPtView('invoiceDetail'); fetchPtInvoiceDetail(inv.id); }}>View / Pay</button>
 </td>
 </tr>
 );
 })}
 </tbody></table></div>
 )}
 </div>
 )}

 {/* INVOICE DETAIL + PAYMENT */}
 {ptView === 'invoiceDetail' && ptSelectedInvoice && (
 <div>
 <button className="btn btn-secondary" onClick={() => { setPtView('invoices'); setPtSelectedInvoice(null); }} style={{marginBottom:16}}>Back to Invoices</button>

 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:20,marginBottom:24}}>
 <div style={{background:'#fff',padding:20,borderRadius:8,boxShadow:'0 1px 3px rgba(0,0,0,.1)'}}>
 <h3 style={{marginTop:0}}>Invoice {ptSelectedInvoice.invoice_number}</h3>
 <table style={{width:'100%',fontSize:14}}><tbody>
 <tr><td style={{padding:6,color:'#888'}}>Customer</td><td style={{padding:6,fontWeight:600}}>{ptSelectedInvoice.customer?.name || ptSelectedInvoice.customer_name || '-'}</td></tr>
 <tr><td style={{padding:6,color:'#888'}}>Order #</td><td style={{padding:6}}>{ptSelectedInvoice.order?.order_number || ptSelectedInvoice.order_number || '-'}</td></tr>
 <tr><td style={{padding:6,color:'#888'}}>Invoice Date</td><td style={{padding:6}}>{ptSelectedInvoice.invoice_date}</td></tr>
 <tr><td style={{padding:6,color:'#888'}}>Due Date</td><td style={{padding:6,color: ptSelectedInvoice.is_overdue ? '#e74c3c' : 'inherit',fontWeight: ptSelectedInvoice.is_overdue ? 700 : 400}}>{ptSelectedInvoice.due_date} {ptSelectedInvoice.is_overdue && `(${ptSelectedInvoice.days_overdue}d overdue)`}</td></tr>
 <tr><td style={{padding:6,color:'#888'}}>Status</td><td style={{padding:6}}><span style={{background: ptSelectedInvoice.status==='paid'?'#27ae60':ptSelectedInvoice.status==='partial'?'#f39c12':'#3498db',color:'#fff',padding:'3px 10px',borderRadius:12,fontSize:11,fontWeight:600,textTransform:'uppercase'}}>{ptSelectedInvoice.is_overdue && ptSelectedInvoice.status!=='paid' ? 'OVERDUE' : ptSelectedInvoice.status}</span></td></tr>
 </tbody></table>
 </div>
 <div style={{background:'#fff',padding:20,borderRadius:8,boxShadow:'0 1px 3px rgba(0,0,0,.1)'}}>
 <h3 style={{marginTop:0}}>Payment Summary</h3>
 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:12,textAlign:'center'}}>
 <div><div style={{fontSize:22,fontWeight:700,color:'#2c3e50'}}>{formatCurrency(ptSelectedInvoice.total_amount)}</div><div style={{color:'#888',fontSize:12}}>Total</div></div>
 <div><div style={{fontSize:22,fontWeight:700,color:'#27ae60'}}>{formatCurrency(ptSelectedInvoice.total_paid||0)}</div><div style={{color:'#888',fontSize:12}}>Paid</div></div>
 <div><div style={{fontSize:22,fontWeight:700,color:'#e74c3c'}}>{formatCurrency(ptSelectedInvoice.balance||0)}</div><div style={{color:'#888',fontSize:12}}>Balance</div></div>
 </div>
 </div>
 </div>

 {/* Invoice Lines */}
 {ptSelectedInvoice.lines && ptSelectedInvoice.lines.length > 0 && (
 <div style={{background:'#fff',padding:20,borderRadius:8,boxShadow:'0 1px 3px rgba(0,0,0,.1)',marginBottom:20}}>
 <h4 style={{marginTop:0}}>Invoice Items</h4>
 <table className="data-table"><thead><tr><th>Product</th><th>Qty</th><th>Unit Price</th><th>Line Total</th></tr></thead><tbody>
 {ptSelectedInvoice.lines.map((l,i) => (
 <tr key={i}><td>{l.product_name||'-'}</td><td>{l.quantity}</td><td>{formatCurrency(l.unit_price)}</td><td>{formatCurrency(l.line_total)}</td></tr>
 ))}
 </tbody></table>
 </div>
 )}

 {/* Payment History */}
 <div style={{background:'#fff',padding:20,borderRadius:8,boxShadow:'0 1px 3px rgba(0,0,0,.1)',marginBottom:20}}>
 <h4 style={{marginTop:0}}>Payment History</h4>
 {(!ptSelectedInvoice.payments || ptSelectedInvoice.payments.length === 0) ? (
 <p style={{color:'#888',textAlign:'center',padding:10}}>No payments recorded yet</p>
 ) : (
 <table className="data-table"><thead><tr><th>#</th><th>Date</th><th>Amount</th><th>Method</th><th>Reference</th><th>Running Balance</th><th>Action</th></tr></thead><tbody>
 {ptSelectedInvoice.payments.map((p,i) => (
 <tr key={p.id}>
 <td>{i+1}</td>
 <td>{p.payment_date}</td>
 <td style={{color:'#27ae60',fontWeight:600}}>{formatCurrency(p.amount)}</td>
 <td style={{textTransform:'capitalize'}}>{(p.payment_method||'').replace(/_/g,' ')}</td>
 <td>{p.reference || '-'}</td>
 <td style={{fontWeight:600}}>{formatCurrency(p.running_balance)}</td>
 <td><button className="btn btn-sm" style={{background:'#e74c3c',color:'#fff',border:'none',fontSize:11}} onClick={() => deletePtPayment(p.id, ptSelectedInvoice.id)}>Delete</button></td>
 </tr>
 ))}
 </tbody></table>
 )}
 </div>

 {/* Record Payment Form */}
 {ptSelectedInvoice.status !== 'paid' && (
 <div style={{background:'#f0f7ff',padding:20,borderRadius:8,border:'2px solid #3498db'}}>
 <h4 style={{marginTop:0,color:'#2980b9'}}>Record Payment</h4>
 <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(180px,1fr))',gap:12}}>
 <div>
 <label style={{fontSize:12,fontWeight:600,display:'block',marginBottom:4}}>Amount *</label>
 <input type="number" step="0.01" className="form-control" placeholder={`Max: ${formatCurrency(ptSelectedInvoice.balance||0)}`} value={ptPaymentForm.amount} onChange={e => setPtPaymentForm(f => ({...f, amount: e.target.value}))} />
 </div>
 <div>
 <label style={{fontSize:12,fontWeight:600,display:'block',marginBottom:4}}>Payment Method</label>
 <select className="form-control" value={ptPaymentForm.payment_method} onChange={e => setPtPaymentForm(f => ({...f, payment_method: e.target.value}))}>
 <option value="bank_transfer">Bank Transfer</option>
 <option value="cash">Cash</option>
 <option value="card">Card</option>
 <option value="cheque">Cheque</option>
 <option value="mobile_money">Mobile Money</option>
 <option value="pos">POS</option>
 </select>
 </div>
 <div>
 <label style={{fontSize:12,fontWeight:600,display:'block',marginBottom:4}}>Payment Date</label>
 <input type="date" className="form-control" value={ptPaymentForm.payment_date} onChange={e => setPtPaymentForm(f => ({...f, payment_date: e.target.value}))} />
 </div>
 <div>
 <label style={{fontSize:12,fontWeight:600,display:'block',marginBottom:4}}>Reference / Receipt #</label>
 <input type="text" className="form-control" placeholder="e.g. TRF-12345" value={ptPaymentForm.reference} onChange={e => setPtPaymentForm(f => ({...f, reference: e.target.value}))} />
 </div>
 <div>
 <label style={{fontSize:12,fontWeight:600,display:'block',marginBottom:4}}>Notes</label>
 <input type="text" className="form-control" placeholder="Optional notes" value={ptPaymentForm.notes} onChange={e => setPtPaymentForm(f => ({...f, notes: e.target.value}))} />
 </div>
 </div>
 <div style={{marginTop:16,display:'flex',gap:8}}>
 <button className="btn btn-primary" onClick={() => recordPtPayment(ptSelectedInvoice.id)} disabled={loading}>
 {loading ? 'Recording...' : 'Record Payment'}
 </button>
 <button className="btn btn-secondary" onClick={() => setPtPaymentForm(f => ({...f, amount: ptSelectedInvoice.balance||0}))}>
 Pay Full Balance ({formatCurrency(ptSelectedInvoice.balance||0)})
 </button>
 </div>
 </div>
 )}
 </div>
 )}

 {/* DEBTORS LIST */}
 {ptView === 'debtors' && (
 <div>
 <h3 style={{marginBottom:12}}>All Debtors ({ptDebtors.length})</h3>
 {ptDebtors.length === 0 ? (
 <div style={{background:'#d4edda',padding:20,borderRadius:8,textAlign:'center',color:'#155724'}}>No outstanding debts!</div>
 ) : (
 <div className="table-responsive"><table className="data-table"><thead><tr>
 <th>Customer</th><th>Phone</th><th>Email</th><th>Invoices</th><th>Total Owed</th><th>Total Paid</th><th>Balance</th><th>Status</th><th>Actions</th>
 </tr></thead><tbody>
 {ptDebtors.map(d => (
 <tr key={d.customer_id} style={{background: d.is_overdue ? '#fff5f5' : '#fff'}}>
 <td><strong>{d.customer_name}</strong></td>
 <td>{d.phone || '-'}</td>
 <td>{d.email || '-'}</td>
 <td>{d.invoice_count}</td>
 <td>{formatCurrency(d.total_invoiced)}</td>
 <td style={{color:'#27ae60'}}>{formatCurrency(d.total_paid)}</td>
 <td style={{color:'#e74c3c',fontWeight:700}}>{formatCurrency(d.balance)}</td>
 <td>{d.is_overdue ? <span style={{color:'#e74c3c',fontWeight:600}}>OVERDUE ({d.days_overdue}d)</span> : <span style={{color:'#f39c12'}}>Outstanding</span>}</td>
 <td style={{whiteSpace:'nowrap'}}>
 <button className="btn btn-sm btn-primary" onClick={() => { setPtView('debtorDetail'); fetchPtDebtorDetail(d.customer_id); }} style={{marginRight:4}}>Details</button>
 <button className="btn btn-sm" onClick={() => { fetchPtReminderMsg(d.customer_id); setPtView('reminder'); }} style={{background:'#25D366',color:'#fff',border:'none',marginRight:4}}>WhatsApp</button>
 </td>
 </tr>
 ))}
 </tbody></table></div>
 )}
 </div>
 )}

 {/* DEBTOR DETAIL */}
 {ptView === 'debtorDetail' && ptSelectedDebtor && (
 <div>
 <button className="btn btn-secondary" onClick={() => { setPtView('debtors'); setPtSelectedDebtor(null); }} style={{marginBottom:16}}>Back to Debtors</button>

 <div style={{background:'#fff',padding:20,borderRadius:8,boxShadow:'0 1px 3px rgba(0,0,0,.1)',marginBottom:20}}>
 <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
 <div>
 <h3 style={{margin:0}}>{ptSelectedDebtor.customer?.name || 'Customer'}</h3>
 <p style={{margin:'4px 0',color:'#888'}}>{ptSelectedDebtor.customer?.phone || ''} {ptSelectedDebtor.customer?.email ? `| ${ptSelectedDebtor.customer.email}` : ''}</p>
 </div>
 <button className="btn btn-sm" onClick={() => { fetchPtReminderMsg(ptSelectedDebtor.customer?.id); setPtView('reminder'); }} style={{background:'#25D366',color:'#fff',border:'none',padding:'8px 16px'}}>Send WhatsApp Reminder</button>
 </div>
 </div>

 {ptSelectedDebtor.summary && (
 <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(180px,1fr))',gap:16,marginBottom:20}}>
 <div style={{background:'#fff',padding:16,borderRadius:8,boxShadow:'0 1px 3px rgba(0,0,0,.1)',textAlign:'center'}}>
 <div style={{fontSize:22,fontWeight:700,color:'#2c3e50'}}>{ptSelectedDebtor.summary.total_invoices}</div><div style={{color:'#888',fontSize:12}}>Invoices</div>
 </div>
 <div style={{background:'#fff',padding:16,borderRadius:8,boxShadow:'0 1px 3px rgba(0,0,0,.1)',textAlign:'center'}}>
 <div style={{fontSize:22,fontWeight:700,color:'#3498db'}}>{formatCurrency(ptSelectedDebtor.summary.total_invoiced)}</div><div style={{color:'#888',fontSize:12}}>Total Invoiced</div>
 </div>
 <div style={{background:'#fff',padding:16,borderRadius:8,boxShadow:'0 1px 3px rgba(0,0,0,.1)',textAlign:'center'}}>
 <div style={{fontSize:22,fontWeight:700,color:'#27ae60'}}>{formatCurrency(ptSelectedDebtor.summary.total_paid)}</div><div style={{color:'#888',fontSize:12}}>Total Paid</div>
 </div>
 <div style={{background:'#fff',padding:16,borderRadius:8,boxShadow:'0 1px 3px rgba(0,0,0,.1)',textAlign:'center'}}>
 <div style={{fontSize:22,fontWeight:700,color:'#e74c3c'}}>{formatCurrency(ptSelectedDebtor.summary.outstanding_balance)}</div><div style={{color:'#888',fontSize:12}}>Outstanding</div>
 </div>
 </div>
 )}

 <h4>Invoices</h4>
 {ptSelectedDebtor.invoices && ptSelectedDebtor.invoices.length > 0 ? (
 <div className="table-responsive" style={{marginBottom:20}}><table className="data-table"><thead><tr>
 <th>Invoice #</th><th>Date</th><th>Due Date</th><th>Total</th><th>Paid</th><th>Balance</th><th>Status</th><th>Action</th>
 </tr></thead><tbody>
 {ptSelectedDebtor.invoices.map(inv => (
 <tr key={inv.id} style={{background: inv.is_overdue ? '#fff5f5' : '#fff'}}>
 <td><strong>{inv.invoice_number}</strong></td>
 <td>{inv.invoice_date}</td>
 <td>{inv.due_date}</td>
 <td>{formatCurrency(inv.total_amount)}</td>
 <td style={{color:'#27ae60'}}>{formatCurrency(inv.paid_amount||0)}</td>
 <td style={{color:'#e74c3c',fontWeight:700}}>{formatCurrency(inv.balance||0)}</td>
 <td><span style={{background: inv.is_overdue && inv.status!=='paid' ? '#e74c3c' : inv.status==='paid'?'#27ae60':inv.status==='partial'?'#f39c12':'#3498db', color:'#fff', padding:'3px 10px', borderRadius:12, fontSize:11, fontWeight:600}}>{inv.is_overdue && inv.status!=='paid' ? 'OVERDUE' : inv.status?.toUpperCase()}</span></td>
 <td><button className="btn btn-sm btn-primary" onClick={() => { setPtView('invoiceDetail'); fetchPtInvoiceDetail(inv.id); }}>Pay / View</button></td>
 </tr>
 ))}
 </tbody></table></div>
 ) : <p style={{color:'#888'}}>No invoices found</p>}

 <h4>All Payments</h4>
 {ptSelectedDebtor.payments && ptSelectedDebtor.payments.length > 0 ? (
 <div className="table-responsive"><table className="data-table"><thead><tr>
 <th>#</th><th>Date</th><th>Invoice #</th><th>Amount</th><th>Method</th><th>Reference</th>
 </tr></thead><tbody>
 {ptSelectedDebtor.payments.map((p,i) => (
 <tr key={p.id}>
 <td>{i+1}</td>
 <td>{p.payment_date}</td>
 <td>{p.invoice_number||'-'}</td>
 <td style={{color:'#27ae60',fontWeight:600}}>{formatCurrency(p.amount)}</td>
 <td style={{textTransform:'capitalize'}}>{(p.payment_method||'').replace(/_/g,' ')}</td>
 <td>{p.reference || '-'}</td>
 </tr>
 ))}
 </tbody></table></div>
 ) : <p style={{color:'#888'}}>No payments recorded</p>}

 {/* LEGACY / PREVIOUS DEBTS IN DEBTOR VIEW */}
 {ptSelectedDebtor.legacy_debts && ptSelectedDebtor.legacy_debts.length > 0 && (
 <div style={{marginTop:24}}>
 <h4 style={{color:'#8e44ad'}}>Previous / Outstanding Debts</h4>
 <div className="table-responsive" style={{marginBottom:20}}><table className="data-table"><thead><tr>
 <th>Debt #</th><th>Description</th><th>Date</th><th>Original</th><th>Paid</th><th>Balance</th><th>Status</th><th>Action</th>
 </tr></thead><tbody>
 {ptSelectedDebtor.legacy_debts.map(ld => (
 <tr key={ld.id} style={{background: ld.status === 'paid' ? '#f0fff0' : '#fdf5ff'}}>
 <td><strong>{ld.debt_number}</strong></td>
 <td>{ld.description}</td>
 <td>{ld.debt_date}</td>
 <td>{formatCurrency(ld.original_amount)}</td>
 <td style={{color:'#27ae60'}}>{formatCurrency(ld.paid_amount||0)}</td>
 <td style={{color:'#e74c3c',fontWeight:700}}>{formatCurrency((ld.original_amount||0)-(ld.paid_amount||0))}</td>
 <td><span style={{background: ld.status==='paid'?'#27ae60':ld.status==='partial'?'#f39c12':'#e74c3c', color:'#fff', padding:'3px 10px', borderRadius:12, fontSize:11, fontWeight:600}}>{(ld.status||'unpaid').toUpperCase()}</span></td>
 <td><button className="btn btn-sm btn-primary" onClick={() => { setPtView('legacyDebtDetail'); fetchLegacyDebtDetail(ld.id); }}>Pay / View</button></td>
 </tr>
 ))}
 </tbody></table></div>
 </div>
 )}
 </div>
 )}

 {/* OVERDUE REMINDERS */}
 {ptView === 'reminders' && (
 <div>
 <h3 style={{marginBottom:12}}>Overdue Reminders ({ptReminders.length})</h3>
 {ptReminders.length === 0 ? (
 <div style={{background:'#d4edda',padding:20,borderRadius:8,textAlign:'center',color:'#155724'}}>No overdue invoices! All payments are current.</div>
 ) : (
 <div className="table-responsive"><table className="data-table"><thead><tr>
 <th>Customer</th><th>Invoice #</th><th>Due Date</th><th>Days Overdue</th><th>Balance</th><th>Action</th>
 </tr></thead><tbody>
 {ptReminders.map(r => (
 <tr key={r.invoice_id} style={{background:'#fff5f5'}}>
 <td><strong>{r.customer_name}</strong></td>
 <td>{r.invoice_number}</td>
 <td>{r.due_date}</td>
 <td style={{color:'#e74c3c',fontWeight:600}}>{r.days_overdue} days</td>
 <td style={{color:'#e74c3c',fontWeight:700}}>{formatCurrency(r.balance)}</td>
 <td style={{whiteSpace:'nowrap'}}>
 <button className="btn btn-sm btn-primary" onClick={() => { setPtView('invoiceDetail'); fetchPtInvoiceDetail(r.invoice_id); }} style={{marginRight:4}}>Pay</button>
 <button className="btn btn-sm" onClick={() => { fetchPtReminderMsg(r.customer_id); setPtView('reminder'); }} style={{background:'#25D366',color:'#fff',border:'none'}}>WhatsApp</button>
 </td>
 </tr>
 ))}
 </tbody></table></div>
 )}
 </div>
 )}

 {/* WHATSAPP REMINDER */}
 {ptView === 'reminder' && ptReminderMsg && (
 <div>
 <button className="btn btn-secondary" onClick={() => setPtView('debtors')} style={{marginBottom:16}}>Back to Debtors</button>

 <div style={{background:'#fff',padding:24,borderRadius:8,boxShadow:'0 1px 3px rgba(0,0,0,.1)',maxWidth:700}}>
 <h3 style={{marginTop:0,display:'flex',alignItems:'center',gap:8}}>
 <span style={{fontSize:22}}>WhatsApp Reminder for {ptReminderMsg.customer_name}</span>
 {ptReminderMsg.customer_phone && <span style={{fontSize:13,color:'#888',fontWeight:400}}>({ptReminderMsg.customer_phone})</span>}
 </h3>

 {ptReminderMsg.summary && (
 <div style={{marginBottom:20,padding:16,background:'linear-gradient(135deg,#f8f9fa,#e8f5e9)',borderRadius:10,border:'1px solid #e0e0e0'}}>
 <h4 style={{marginTop:0,marginBottom:12,fontSize:14,color:'#555'}}>Account Summary</h4>
 <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(120px,1fr))',gap:12,textAlign:'center'}}>
 <div style={{padding:8,background:'#fff',borderRadius:8}}>
 <strong style={{color:'#3498db',fontSize:16}}>{formatCurrency(ptReminderMsg.summary.total_invoiced)}</strong>
 <br /><span style={{fontSize:11,color:'#888'}}>Total Invoiced</span>
 </div>
 <div style={{padding:8,background:'#fff',borderRadius:8}}>
 <strong style={{color:'#27ae60',fontSize:16}}>{formatCurrency(ptReminderMsg.summary.total_paid)}</strong>
 <br /><span style={{fontSize:11,color:'#888'}}>Total Paid</span>
 </div>
 <div style={{padding:8,background:'#fff',borderRadius:8}}>
 <strong style={{color:'#e74c3c',fontSize:16}}>{formatCurrency(ptReminderMsg.summary.outstanding_balance)}</strong>
 <br /><span style={{fontSize:11,color:'#888'}}>Outstanding</span>
 </div>
 <div style={{padding:8,background:'#fff',borderRadius:8}}>
 <strong style={{color:'#f39c12',fontSize:16}}>{ptReminderMsg.summary.total_invoices}</strong>
 <br /><span style={{fontSize:11,color:'#888'}}>Invoices</span>
 </div>
 {ptReminderMsg.summary.overdue_invoices > 0 && (
 <div style={{padding:8,background:'#fff3e0',borderRadius:8,border:'1px solid #ffcc80'}}>
 <strong style={{color:'#e65100',fontSize:16}}>{ptReminderMsg.summary.overdue_invoices}</strong>
 <br /><span style={{fontSize:11,color:'#e65100'}}>Overdue</span>
 </div>
 )}
 </div>
 </div>
 )}

 <div style={{background:'#e8f5e9',padding:16,borderRadius:8,marginBottom:16,fontFamily:'monospace',whiteSpace:'pre-wrap',fontSize:13,lineHeight:1.6,border:'1px solid #c8e6c9',maxHeight:500,overflowY:'auto'}}>
 {ptReminderMsg.message || ptReminderMsg.whatsapp_message || 'No message generated.'}
 </div>
 <div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
 {ptReminderMsg.whatsapp_url && (
 <a href={ptReminderMsg.whatsapp_url} target="_blank" rel="noopener noreferrer" className="btn" style={{background:'#25D366',color:'#fff',border:'none',padding:'10px 24px',textDecoration:'none',borderRadius:6,fontWeight:600,display:'flex',alignItems:'center',gap:6}}>
 Open WhatsApp
 </a>
 )}
 <button className="btn btn-secondary" onClick={() => {
 const msg = ptReminderMsg.message || ptReminderMsg.whatsapp_message || '';
 navigator.clipboard.writeText(msg);
 notify('Message copied to clipboard!', 'success');
 }}>
 Copy Message
 </button>
 </div>
 </div>
 </div>
 )}

 {/* ========== PREVIOUS / LEGACY DEBTS LIST ========== */}
 {ptView === 'legacyDebts' && (
 <div>
 <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16,flexWrap:'wrap',gap:8}}>
 <h3 style={{margin:0}}>Previous / Outstanding Debts ({legacyDebts.length})</h3>
 <button className="btn btn-primary" onClick={() => setPtView('addLegacyDebt')}>+ Record Previous Debt</button>
 </div>

 {legacyDebtsSummary && (
 <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(160px,1fr))',gap:16,marginBottom:20}}>
 <div style={{background:'linear-gradient(135deg,#8e44ad,#9b59b6)',padding:16,borderRadius:10,textAlign:'center',color:'#fff'}}>
 <div style={{fontSize:22,fontWeight:700}}>{legacyDebtsSummary.total_debts || 0}</div><div style={{fontSize:12,opacity:.8}}>Total Previous Debts</div>
 </div>
 <div style={{background:'linear-gradient(135deg,#2c3e50,#34495e)',padding:16,borderRadius:10,textAlign:'center',color:'#fff'}}>
 <div style={{fontSize:22,fontWeight:700}}>{formatCurrency(legacyDebtsSummary.total_original || 0)}</div><div style={{fontSize:12,opacity:.8}}>Total Amount Owed</div>
 </div>
 <div style={{background:'linear-gradient(135deg,#27ae60,#2ecc71)',padding:16,borderRadius:10,textAlign:'center',color:'#fff'}}>
 <div style={{fontSize:22,fontWeight:700}}>{formatCurrency(legacyDebtsSummary.total_paid || 0)}</div><div style={{fontSize:12,opacity:.8}}>Total Paid</div>
 </div>
 <div style={{background:'linear-gradient(135deg,#e74c3c,#c0392b)',padding:16,borderRadius:10,textAlign:'center',color:'#fff'}}>
 <div style={{fontSize:22,fontWeight:700}}>{formatCurrency(legacyDebtsSummary.total_balance || 0)}</div><div style={{fontSize:12,opacity:.8}}>Outstanding Balance</div>
 </div>
 </div>
 )}

 {legacyDebts.length === 0 ? (
 <div style={{background:'#f8f9fa',padding:40,borderRadius:8,textAlign:'center',color:'#888'}}>
 <p style={{fontSize:16,marginBottom:12}}>No previous debts recorded yet.</p>
 <p>Click "Record Previous Debt" to add debts that existed before this system was set up.</p>
 </div>
 ) : (
 <div className="table-responsive"><table className="data-table"><thead><tr>
 <th>Debt #</th><th>Customer</th><th>Description</th><th>Debt Date</th><th>Original Amount</th><th>Paid</th><th>Balance</th><th>Status</th><th>Actions</th>
 </tr></thead><tbody>
 {legacyDebts.map(d => (
 <tr key={d.id} style={{background: d.status === 'paid' ? '#f0fff0' : d.status === 'partial' ? '#fff8e1' : '#fff5f5'}}>
 <td><strong>{d.debt_number}</strong></td>
 <td>{d.customer_name || 'Unknown'}</td>
 <td style={{maxWidth:200,overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{d.description}</td>
 <td>{d.debt_date}</td>
 <td>{formatCurrency(d.original_amount)}</td>
 <td style={{color:'#27ae60'}}>{formatCurrency(d.paid_amount||0)}</td>
 <td style={{color:'#e74c3c',fontWeight:700}}>{formatCurrency(d.balance||0)}</td>
 <td><span style={{background: d.status==='paid'?'#27ae60':d.status==='partial'?'#f39c12':'#e74c3c', color:'#fff', padding:'3px 10px', borderRadius:12, fontSize:11, fontWeight:600}}>{(d.status||'unpaid').toUpperCase()}</span></td>
 <td style={{whiteSpace:'nowrap'}}>
 <button className="btn btn-sm btn-primary" onClick={() => { setPtView('legacyDebtDetail'); fetchLegacyDebtDetail(d.id); }} style={{marginRight:4}}>View / Pay</button>
 <button className="btn btn-sm" onClick={() => deleteLegacyDebt(d.id)} style={{background:'#e74c3c',color:'#fff',border:'none'}}>Delete</button>
 </td>
 </tr>
 ))}
 </tbody></table></div>
 )}
 </div>
 )}

 {/* ========== ADD NEW PREVIOUS DEBT FORM ========== */}
 {ptView === 'addLegacyDebt' && (
 <div>
 <button className="btn btn-secondary" onClick={() => setPtView('legacyDebts')} style={{marginBottom:16}}>Back to Previous Debts</button>
 <div style={{background:'#fff',padding:24,borderRadius:8,boxShadow:'0 1px 3px rgba(0,0,0,.1)',maxWidth:700}}>
 <h3 style={{marginTop:0,color:'#8e44ad'}}>Record Previous / Outstanding Debt</h3>
 <p style={{color:'#888',fontSize:13,marginBottom:20}}>Use this form to record debts that existed before this system was set up, or any outstanding debts that are not linked to a system invoice.</p>
 <form onSubmit={(e) => { e.preventDefault(); createLegacyDebt(); }}>
 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16}}>
 <div className="form-group" style={{gridColumn:'1 / -1'}}>
 <label>Customer *</label>
 <select value={legacyDebtForm.customer_id} onChange={(e) => setLegacyDebtForm(f => ({...f, customer_id: e.target.value}))} required style={{width:'100%',padding:8,borderRadius:4,border:'1px solid #ddd'}}>
 <option value="">Select customer</option>
 {(data.customers||[]).map(c => <option key={c.id} value={c.id}>{c.name}{c.phone ? ` (${c.phone})` : ''}</option>)}
 </select>
 </div>
 <div className="form-group" style={{gridColumn:'1 / -1'}}>
 <label>Description / Reason for Debt *</label>
 <textarea value={legacyDebtForm.description} onChange={(e) => setLegacyDebtForm(f => ({...f, description: e.target.value}))} required rows={3} style={{width:'100%',padding:8,borderRadius:4,border:'1px solid #ddd'}} placeholder="e.g., Outstanding balance from previous transactions, goods supplied in 2023..." />
 </div>
 <div className="form-group">
 <label>Original Amount Owed (Naira) *</label>
 <input type="number" step="0.01" min="0" value={legacyDebtForm.original_amount} onChange={(e) => setLegacyDebtForm(f => ({...f, original_amount: e.target.value}))} required style={{width:'100%',padding:8,borderRadius:4,border:'1px solid #ddd'}} placeholder="0.00" />
 </div>
 <div className="form-group">
 <label>Amount Already Paid</label>
 <input type="number" step="0.01" min="0" value={legacyDebtForm.amount_already_paid} onChange={(e) => setLegacyDebtForm(f => ({...f, amount_already_paid: e.target.value}))} style={{width:'100%',padding:8,borderRadius:4,border:'1px solid #ddd'}} placeholder="0.00" />
 <small style={{color:'#888'}}>If the customer has already made partial payment towards this debt</small>
 </div>
 <div className="form-group">
 <label>Debt Date *</label>
 <input type="date" value={legacyDebtForm.debt_date} onChange={(e) => setLegacyDebtForm(f => ({...f, debt_date: e.target.value}))} required style={{width:'100%',padding:8,borderRadius:4,border:'1px solid #ddd'}} />
 <small style={{color:'#888'}}>When was this debt originally incurred?</small>
 </div>
 <div className="form-group">
 <label>Due Date</label>
 <input type="date" value={legacyDebtForm.due_date} onChange={(e) => setLegacyDebtForm(f => ({...f, due_date: e.target.value}))} style={{width:'100%',padding:8,borderRadius:4,border:'1px solid #ddd'}} />
 </div>
 <div className="form-group" style={{gridColumn:'1 / -1'}}>
 <label>Notes</label>
 <textarea value={legacyDebtForm.notes} onChange={(e) => setLegacyDebtForm(f => ({...f, notes: e.target.value}))} rows={2} style={{width:'100%',padding:8,borderRadius:4,border:'1px solid #ddd'}} placeholder="Any additional notes..." />
 </div>
 </div>
 {legacyDebtForm.original_amount && parseFloat(legacyDebtForm.original_amount) > 0 && (
 <div style={{background:'#f8f9fa',padding:16,borderRadius:8,marginTop:16,marginBottom:16}}>
 <strong>Summary:</strong> Debt of {formatCurrency(parseFloat(legacyDebtForm.original_amount)||0)}
 {parseFloat(legacyDebtForm.amount_already_paid) > 0 && ` with ${formatCurrency(parseFloat(legacyDebtForm.amount_already_paid))} already paid`}
 {parseFloat(legacyDebtForm.amount_already_paid) > 0 && ` = ${formatCurrency((parseFloat(legacyDebtForm.original_amount)||0) - (parseFloat(legacyDebtForm.amount_already_paid)||0))} outstanding`}
 </div>
 )}
 <button type="submit" className="btn btn-primary" disabled={loading} style={{padding:'10px 32px',fontSize:15}}>
 {loading ? 'Saving...' : 'Record Previous Debt'}
 </button>
 </form>
 </div>
 </div>
 )}

 {/* ========== LEGACY DEBT DETAIL + PAYMENT ========== */}
 {ptView === 'legacyDebtDetail' && legacyDebtDetail && (
 <div>
 <button className="btn btn-secondary" onClick={() => { setPtView('legacyDebts'); setLegacyDebtDetail(null); }} style={{marginBottom:16}}>Back to Previous Debts</button>

 <div style={{background:'#fff',padding:20,borderRadius:8,boxShadow:'0 1px 3px rgba(0,0,0,.1)',marginBottom:20}}>
 <div style={{display:'flex',justifyContent:'space-between',alignItems:'flex-start',flexWrap:'wrap',gap:12}}>
 <div>
 <h3 style={{margin:0,color:'#8e44ad'}}>Previous Debt {legacyDebtDetail.debt_number}</h3>
 <p style={{margin:'6px 0 0',fontSize:14,color:'#555'}}><strong>Customer:</strong> {legacyDebtDetail.customer_name || 'Unknown'}</p>
 <p style={{margin:'4px 0',fontSize:13,color:'#888'}}>{legacyDebtDetail.description}</p>
 <p style={{margin:'4px 0',fontSize:12,color:'#aaa'}}>Debt Date: {legacyDebtDetail.debt_date}{legacyDebtDetail.due_date ? ` | Due: ${legacyDebtDetail.due_date}` : ''}</p>
 {legacyDebtDetail.notes && <p style={{margin:'4px 0',fontSize:12,color:'#aaa',fontStyle:'italic'}}>Notes: {legacyDebtDetail.notes}</p>}
 </div>
 <div style={{display:'flex',gap:8}}>
 <span style={{background: legacyDebtDetail.status==='paid'?'#27ae60':legacyDebtDetail.status==='partial'?'#f39c12':'#e74c3c', color:'#fff', padding:'6px 16px', borderRadius:20, fontSize:13, fontWeight:600}}>{(legacyDebtDetail.status||'unpaid').toUpperCase()}</span>
 <button className="btn btn-sm" onClick={() => deleteLegacyDebt(legacyDebtDetail.id)} style={{background:'#e74c3c',color:'#fff',border:'none'}}>Delete Debt</button>
 </div>
 </div>
 </div>

 <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(160px,1fr))',gap:16,marginBottom:20}}>
 <div style={{background:'#fff',padding:16,borderRadius:8,boxShadow:'0 1px 3px rgba(0,0,0,.1)',textAlign:'center'}}>
 <div style={{fontSize:22,fontWeight:700,color:'#2c3e50'}}>{formatCurrency(legacyDebtDetail.original_amount)}</div><div style={{color:'#888',fontSize:12}}>Original Amount</div>
 </div>
 <div style={{background:'#fff',padding:16,borderRadius:8,boxShadow:'0 1px 3px rgba(0,0,0,.1)',textAlign:'center'}}>
 <div style={{fontSize:22,fontWeight:700,color:'#27ae60'}}>{formatCurrency(legacyDebtDetail.paid_amount||0)}</div><div style={{color:'#888',fontSize:12}}>Total Paid</div>
 </div>
 <div style={{background:'#fff',padding:16,borderRadius:8,boxShadow:'0 1px 3px rgba(0,0,0,.1)',textAlign:'center'}}>
 <div style={{fontSize:22,fontWeight:700,color:'#e74c3c'}}>{formatCurrency(legacyDebtDetail.balance||0)}</div><div style={{color:'#888',fontSize:12}}>Outstanding Balance</div>
 </div>
 </div>

 {/* RECORD PAYMENT FORM */}
 {legacyDebtDetail.status !== 'paid' && (
 <div style={{background:'#f0f7ff',padding:20,borderRadius:8,marginBottom:20,border:'1px solid #d0e3ff'}}>
 <h4 style={{marginTop:0,color:'#2c3e50'}}>Record Payment Against This Debt</h4>
 <form onSubmit={(e) => { e.preventDefault(); recordLegacyPayment(legacyDebtDetail.id); }} style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(180px,1fr))',gap:12,alignItems:'end'}}>
 <div className="form-group" style={{margin:0}}>
 <label style={{fontSize:12}}>Amount (Naira) *</label>
 <input type="number" step="0.01" min="0.01" max={legacyDebtDetail.balance||999999999} value={legacyPaymentForm.amount} onChange={(e) => setLegacyPaymentForm(f => ({...f, amount: e.target.value}))} required style={{width:'100%',padding:8,borderRadius:4,border:'1px solid #ddd'}} placeholder="0.00" />
 </div>
 <div className="form-group" style={{margin:0}}>
 <label style={{fontSize:12}}>Payment Method</label>
 <select value={legacyPaymentForm.payment_method} onChange={(e) => setLegacyPaymentForm(f => ({...f, payment_method: e.target.value}))} style={{width:'100%',padding:8,borderRadius:4,border:'1px solid #ddd'}}>
 <option value="bank_transfer">Bank Transfer</option>
 <option value="cash">Cash</option>
 <option value="pos">POS</option>
 <option value="cheque">Cheque</option>
 <option value="mobile_money">Mobile Money</option>
 <option value="other">Other</option>
 </select>
 </div>
 <div className="form-group" style={{margin:0}}>
 <label style={{fontSize:12}}>Payment Date</label>
 <input type="date" value={legacyPaymentForm.payment_date} onChange={(e) => setLegacyPaymentForm(f => ({...f, payment_date: e.target.value}))} style={{width:'100%',padding:8,borderRadius:4,border:'1px solid #ddd'}} />
 </div>
 <div className="form-group" style={{margin:0}}>
 <label style={{fontSize:12}}>Reference / Receipt #</label>
 <input type="text" value={legacyPaymentForm.reference} onChange={(e) => setLegacyPaymentForm(f => ({...f, reference: e.target.value}))} style={{width:'100%',padding:8,borderRadius:4,border:'1px solid #ddd'}} placeholder="Ref..." />
 </div>
 <div className="form-group" style={{margin:0}}>
 <label style={{fontSize:12}}>Notes</label>
 <input type="text" value={legacyPaymentForm.notes} onChange={(e) => setLegacyPaymentForm(f => ({...f, notes: e.target.value}))} style={{width:'100%',padding:8,borderRadius:4,border:'1px solid #ddd'}} placeholder="Notes..." />
 </div>
 <button type="submit" className="btn btn-primary" disabled={loading} style={{height:38}}>
 {loading ? 'Recording...' : 'Record Payment'}
 </button>
 </form>
 <p style={{margin:'12px 0 0',fontSize:12,color:'#888'}}>
 Please send evidence of payment via WhatsApp to +234 702 575 5406 for confirmation.
 </p>
 </div>
 )}

 {/* PAYMENT HISTORY */}
 <h4>Payment History</h4>
 {legacyDebtDetail.payments && legacyDebtDetail.payments.length > 0 ? (
 <div className="table-responsive"><table className="data-table"><thead><tr>
 <th>#</th><th>Date</th><th>Amount</th><th>Method</th><th>Reference</th><th>Notes</th><th>Running Balance</th><th>Action</th>
 </tr></thead><tbody>
 {legacyDebtDetail.payments.map((p, i) => (
 <tr key={p.id}>
 <td>{i+1}</td>
 <td>{p.payment_date}</td>
 <td style={{color:'#27ae60',fontWeight:600}}>{formatCurrency(p.amount)}</td>
 <td style={{textTransform:'capitalize'}}>{(p.payment_method||'').replace(/_/g,' ')}</td>
 <td>{p.reference || '-'}</td>
 <td style={{fontSize:12,color:'#888'}}>{p.notes || '-'}</td>
 <td style={{fontWeight:600}}>{p.running_balance !== undefined ? formatCurrency(p.running_balance) : '-'}</td>
 <td><button className="btn btn-sm" onClick={() => deleteLegacyPayment(p.id, legacyDebtDetail.id)} style={{background:'#e74c3c',color:'#fff',border:'none',fontSize:11}}>Delete</button></td>
 </tr>
 ))}
 </tbody></table></div>
 ) : <p style={{color:'#888'}}>No payments recorded yet for this debt.</p>}
 </div>
 )}

 </div>
 )}

 {/* ===================== PROCUREMENT MODULE ===================== */}
 {activeModule === 'procurement' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Procurement & Requests</h2>
 </div>
 <div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
 {['dashboard','newRequest','requests','approved','newOrder','orders','invoices','expenses','newExpense'].map(v => (
 <button key={v} className={`btn ${procView===v?'btn-primary':'btn-secondary'}`} style={{fontSize:12,padding:'6px 12px'}} onClick={()=>setProcView(v)}>
 {v==='dashboard'?'Dashboard':v==='newRequest'?'New Request':v==='requests'?'All Requests':v==='approved'?'Approved':v==='newOrder'?'New PO':v==='orders'?'Purchase Orders':v==='invoices'?'Invoices':v==='expenses'?'Expenses':v==='newExpense'?'Record Expense':v}
 </button>
 ))}
 </div>
 </div>

 {/* Procurement Dashboard */}
 {procView === 'dashboard' && procDashboard && (
 <div>
 <div className="stats-grid" style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(200px,1fr))',gap:16,marginBottom:24}}>
 <div className="stat-card" style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h4 style={{margin:'0 0 8px',color:'#888',fontSize:13}}>Pending Requests</h4>
 <div style={{fontSize:28,fontWeight:700,color:'#f39c12'}}>{procDashboard.requests?.pending || 0}</div>
 </div>
 <div className="stat-card" style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h4 style={{margin:'0 0 8px',color:'#888',fontSize:13}}>Approved Requests</h4>
 <div style={{fontSize:28,fontWeight:700,color:'#27ae60'}}>{procDashboard.requests?.approved || 0}</div>
 </div>
 <div className="stat-card" style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h4 style={{margin:'0 0 8px',color:'#888',fontSize:13}}>Total POs</h4>
 <div style={{fontSize:28,fontWeight:700,color:'#3498db'}}>{procDashboard.orders?.total || 0}</div>
 </div>
 <div className="stat-card" style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h4 style={{margin:'0 0 8px',color:'#888',fontSize:13}}>Total Ordered</h4>
 <div style={{fontSize:22,fontWeight:700,color:'#2c3e50'}}>{formatCurrency(procDashboard.orders?.total_ordered || 0)}</div>
 </div>
 <div className="stat-card" style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h4 style={{margin:'0 0 8px',color:'#888',fontSize:13}}>Total Paid</h4>
 <div style={{fontSize:22,fontWeight:700,color:'#27ae60'}}>{formatCurrency(procDashboard.orders?.total_paid || 0)}</div>
 </div>
 <div className="stat-card" style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h4 style={{margin:'0 0 8px',color:'#888',fontSize:13}}>Outstanding</h4>
 <div style={{fontSize:22,fontWeight:700,color:'#e74c3c'}}>{formatCurrency(procDashboard.orders?.total_outstanding || 0)}</div>
 </div>
 </div>

 {/* Expense categories */}
 {procDashboard.expenses?.categories?.length > 0 && (
 <div style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)',marginBottom:20}}>
 <h3 style={{marginTop:0}}>Expense Breakdown</h3>
 <table className="data-table"><thead><tr><th>Category</th><th>Count</th><th>Total</th></tr></thead>
 <tbody>{procDashboard.expenses.categories.map((c,i) => (
 <tr key={i}><td style={{textTransform:'capitalize'}}>{c.category}</td><td>{c.count}</td><td style={{fontWeight:600}}>{formatCurrency(c.total)}</td></tr>
 ))}</tbody></table>
 <div style={{marginTop:12,fontWeight:700,fontSize:16}}>Total Expenses: {formatCurrency(procDashboard.expenses.total_expenses)}</div>
 </div>
 )}

 {/* Recent requests */}
 {procDashboard.recent_requests?.length > 0 && (
 <div style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h3 style={{marginTop:0}}>Recent Requests</h3>
 <table className="data-table"><thead><tr><th>Request #</th><th>Title</th><th>Category</th><th>Status</th><th>Est. Cost</th></tr></thead>
 <tbody>{procDashboard.recent_requests.map((r,i) => (
 <tr key={i}><td>{r.request_number}</td><td>{r.title}</td><td style={{textTransform:'capitalize'}}>{r.category}</td>
 <td><span style={{padding:'2px 10px',borderRadius:12,fontSize:11,fontWeight:600,background:r.status==='submitted'?'#f39c12':r.status==='approved'?'#27ae60':r.status==='rejected'?'#e74c3c':'#3498db',color:'#fff'}}>{r.status.toUpperCase()}</span></td>
 <td>{formatCurrency(r.total_estimated_cost)}</td></tr>
 ))}</tbody></table>
 </div>
 )}
 </div>
 )}

 {/* New Purchase Request Form */}
 {procView === 'newRequest' && (
 <div style={{background:'#fff',padding:24,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h3 style={{marginTop:0}}>New Purchase Request</h3>
 <form onSubmit={submitProcRequest}>
 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16}}>
 <div><label className="form-label">Requested By *</label><input className="form-input" required value={procRequestForm.requested_by} onChange={e=>setProcRequestForm(p=>({...p,requested_by:e.target.value}))}/></div>
 <div><label className="form-label">Department</label><input className="form-input" value={procRequestForm.department} onChange={e=>setProcRequestForm(p=>({...p,department:e.target.value}))}/></div>
 <div><label className="form-label">Category *</label>
 <select className="form-input" value={procRequestForm.category} onChange={e=>setProcRequestForm(p=>({...p,category:e.target.value}))}>
 <option value="general">General</option><option value="raw_material">Raw Material</option><option value="consumable">Consumable</option>
 <option value="machine">Machine</option><option value="tool">Tool</option><option value="office_supply">Office Supply</option>
 </select>
 </div>
 <div><label className="form-label">Priority</label>
 <select className="form-input" value={procRequestForm.priority} onChange={e=>setProcRequestForm(p=>({...p,priority:e.target.value}))}>
 <option value="low">Low</option><option value="normal">Normal</option><option value="high">High</option><option value="urgent">Urgent</option>
 </select>
 </div>
 <div style={{gridColumn:'1/-1'}}><label className="form-label">Title *</label><input className="form-input" required value={procRequestForm.title} onChange={e=>setProcRequestForm(p=>({...p,title:e.target.value}))}/></div>
 <div style={{gridColumn:'1/-1'}}><label className="form-label">Description</label><textarea className="form-input" rows={2} value={procRequestForm.description} onChange={e=>setProcRequestForm(p=>({...p,description:e.target.value}))}/></div>
 <div style={{gridColumn:'1/-1'}}><label className="form-label">Justification</label><textarea className="form-input" rows={2} value={procRequestForm.justification} onChange={e=>setProcRequestForm(p=>({...p,justification:e.target.value}))}/></div>
 <div><label className="form-label">Vendor Name</label><input className="form-input" value={procRequestForm.vendor_name} onChange={e=>setProcRequestForm(p=>({...p,vendor_name:e.target.value}))}/></div>
 <div><label className="form-label">Vendor Phone</label><input className="form-input" value={procRequestForm.vendor_phone} onChange={e=>setProcRequestForm(p=>({...p,vendor_phone:e.target.value}))}/></div>
 <div><label className="form-label">Vendor Email</label><input className="form-input" value={procRequestForm.vendor_email} onChange={e=>setProcRequestForm(p=>({...p,vendor_email:e.target.value}))}/></div>
 <div><label className="form-label">Expected Delivery</label><input className="form-input" type="date" value={procRequestForm.expected_delivery_date} onChange={e=>setProcRequestForm(p=>({...p,expected_delivery_date:e.target.value}))}/></div>
 </div>

 <h4 style={{marginTop:20}}>Items</h4>
 {procRequestForm.items.map((item, idx) => (
 <div key={idx} style={{display:'grid',gridTemplateColumns:'2fr 1fr 1fr 1fr 60px',gap:8,marginBottom:8,alignItems:'end'}}>
 <div><label className="form-label" style={{fontSize:11}}>Item Name *</label><input className="form-input" required value={item.item_name} onChange={e=>{const items=[...procRequestForm.items];items[idx].item_name=e.target.value;setProcRequestForm(p=>({...p,items}));}}/></div>
 <div><label className="form-label" style={{fontSize:11}}>Qty</label><input className="form-input" type="number" min="1" value={item.quantity} onChange={e=>{const items=[...procRequestForm.items];items[idx].quantity=e.target.value;setProcRequestForm(p=>({...p,items}));}}/></div>
 <div><label className="form-label" style={{fontSize:11}}>Unit</label><input className="form-input" value={item.unit} onChange={e=>{const items=[...procRequestForm.items];items[idx].unit=e.target.value;setProcRequestForm(p=>({...p,items}));}}/></div>
 <div><label className="form-label" style={{fontSize:11}}>Est. Unit Cost</label><input className="form-input" type="number" step="0.01" value={item.estimated_unit_cost} onChange={e=>{const items=[...procRequestForm.items];items[idx].estimated_unit_cost=e.target.value;setProcRequestForm(p=>({...p,items}));}}/></div>
 <button type="button" className="btn btn-danger" style={{padding:'6px 10px',fontSize:12}} onClick={()=>{const items=procRequestForm.items.filter((_,i)=>i!==idx);setProcRequestForm(p=>({...p,items:items.length?items:[{item_type:'general',item_name:'',quantity:'1',unit:'each',estimated_unit_cost:'',specification:''}]}));}}>X</button>
 </div>
 ))}
 <button type="button" className="btn btn-secondary" style={{fontSize:12,marginBottom:16}} onClick={()=>setProcRequestForm(p=>({...p,items:[...p.items,{item_type:p.category==='raw_material'?'raw_material':p.category==='consumable'?'consumable':p.category==='machine'?'machine':'general',item_name:'',quantity:'1',unit:'each',estimated_unit_cost:'',specification:''}]}))}>+ Add Item</button>

 <div><label className="form-label">Notes</label><textarea className="form-input" rows={2} value={procRequestForm.notes} onChange={e=>setProcRequestForm(p=>({...p,notes:e.target.value}))}/></div>
 <div style={{marginTop:16,display:'flex',gap:8}}>
 <button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Submitting...':'Submit Request'}</button>
 <button type="button" className="btn btn-secondary" onClick={()=>setProcView('dashboard')}>Cancel</button>
 </div>
 </form>
 </div>
 )}

 {/* All Requests List */}
 {(procView === 'requests' || procView === 'approved') && !procSelectedRequest && (
 <div style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h3 style={{marginTop:0}}>{procView==='approved'?'Approved Requests':'All Purchase Requests'}</h3>
 <table className="data-table">
 <thead><tr><th>Request #</th><th>Title</th><th>Requested By</th><th>Category</th><th>Priority</th><th>Status</th><th>Est. Cost</th><th>Date</th><th>Actions</th></tr></thead>
 <tbody>
 {(procView==='approved'?procRequests.filter(r=>r.status==='approved'):procRequests).map(r => (
 <tr key={r.id}>
 <td><button className="btn-link" onClick={()=>fetchProcRequestDetail(r.id)}>{r.request_number}</button></td>
 <td>{r.title}</td><td>{r.requested_by}</td>
 <td style={{textTransform:'capitalize'}}>{r.category.replace('_',' ')}</td>
 <td><span style={{padding:'2px 8px',borderRadius:10,fontSize:11,fontWeight:600,background:r.priority==='urgent'?'#e74c3c':r.priority==='high'?'#f39c12':r.priority==='low'?'#95a5a6':'#3498db',color:'#fff'}}>{r.priority.toUpperCase()}</span></td>
 <td><span style={{padding:'2px 10px',borderRadius:12,fontSize:11,fontWeight:600,background:r.status==='submitted'?'#f39c12':r.status==='approved'?'#27ae60':r.status==='rejected'?'#e74c3c':r.status==='ordered'?'#3498db':r.status==='received'?'#2ecc71':'#95a5a6',color:'#fff'}}>{r.status.toUpperCase()}</span></td>
 <td>{formatCurrency(r.total_estimated_cost)}</td>
 <td>{r.created_at?new Date(r.created_at).toLocaleDateString():''}</td>
 <td style={{display:'flex',gap:4}}>
 {r.status==='submitted' && <><button className="btn btn-primary" style={{fontSize:11,padding:'4px 8px'}} onClick={()=>approveProcRequest(r.id)}>Approve</button><button className="btn btn-danger" style={{fontSize:11,padding:'4px 8px'}} onClick={()=>rejectProcRequest(r.id)}>Reject</button></>}
 {r.status==='approved' && <button className="btn btn-secondary" style={{fontSize:11,padding:'4px 8px'}} onClick={()=>{setProcOrderForm(p=>({...p,vendor_name:r.vendor_name||'',request_id:r.id}));setProcView('newOrder');}}>Create PO</button>}
 </td>
 </tr>
 ))}
 {procRequests.length===0 && <tr><td colSpan="9" style={{textAlign:'center',padding:32,color:'#888'}}>No purchase requests found</td></tr>}
 </tbody>
 </table>
 </div>
 )}

 {/* Request Detail */}
 {(procView==='requests'||procView==='approved') && procSelectedRequest && (
 <div style={{background:'#fff',padding:24,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <button className="btn btn-secondary" style={{marginBottom:16}} onClick={()=>setProcSelectedRequest(null)}>Back to List</button>
 <h3 style={{marginTop:0}}>Request: {procSelectedRequest.request_number}</h3>
 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:16,marginBottom:20}}>
 <div><strong>Title:</strong> {procSelectedRequest.title}</div>
 <div><strong>Requested By:</strong> {procSelectedRequest.requested_by}</div>
 <div><strong>Department:</strong> {procSelectedRequest.department}</div>
 <div><strong>Category:</strong> <span style={{textTransform:'capitalize'}}>{procSelectedRequest.category.replace('_',' ')}</span></div>
 <div><strong>Priority:</strong> {procSelectedRequest.priority}</div>
 <div><strong>Status:</strong> <span style={{padding:'2px 10px',borderRadius:12,fontSize:11,fontWeight:600,background:procSelectedRequest.status==='submitted'?'#f39c12':procSelectedRequest.status==='approved'?'#27ae60':procSelectedRequest.status==='rejected'?'#e74c3c':'#3498db',color:'#fff'}}>{procSelectedRequest.status.toUpperCase()}</span></div>
 <div><strong>Est. Cost:</strong> {formatCurrency(procSelectedRequest.total_estimated_cost)}</div>
 <div><strong>Vendor:</strong> {procSelectedRequest.vendor_name||'N/A'}</div>
 <div><strong>Expected Delivery:</strong> {procSelectedRequest.expected_delivery_date||'N/A'}</div>
 </div>
 {procSelectedRequest.description && <div style={{marginBottom:12}}><strong>Description:</strong> {procSelectedRequest.description}</div>}
 {procSelectedRequest.justification && <div style={{marginBottom:12}}><strong>Justification:</strong> {procSelectedRequest.justification}</div>}
 {procSelectedRequest.rejection_reason && <div style={{marginBottom:12,color:'#e74c3c'}}><strong>Rejection Reason:</strong> {procSelectedRequest.rejection_reason}</div>}

 <h4>Items ({procSelectedRequest.items?.length || 0})</h4>
 <table className="data-table"><thead><tr><th>Item</th><th>Type</th><th>Qty</th><th>Unit</th><th>Est. Unit Cost</th><th>Est. Total</th></tr></thead>
 <tbody>{(procSelectedRequest.items||[]).map((it,i)=>(
 <tr key={i}><td>{it.item_name}</td><td style={{textTransform:'capitalize'}}>{it.item_type}</td><td>{it.quantity}</td><td>{it.unit}</td><td>{formatCurrency(it.estimated_unit_cost)}</td><td>{formatCurrency(it.estimated_total)}</td></tr>
 ))}</tbody></table>

 {procSelectedRequest.status==='submitted' && (
 <div style={{marginTop:16,display:'flex',gap:8}}>
 <button className="btn btn-primary" onClick={()=>approveProcRequest(procSelectedRequest.id)}>Approve</button>
 <button className="btn btn-danger" onClick={()=>rejectProcRequest(procSelectedRequest.id)}>Reject</button>
 </div>
 )}
 {procSelectedRequest.status==='approved' && (
 <button className="btn btn-primary" style={{marginTop:16}} onClick={()=>{setProcOrderForm(p=>({...p,vendor_name:procSelectedRequest.vendor_name||'',request_id:procSelectedRequest.id}));setProcView('newOrder');}}>Create Purchase Order</button>
 )}
 </div>
 )}

 {/* New Purchase Order Form */}
 {procView === 'newOrder' && (
 <div style={{background:'#fff',padding:24,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h3 style={{marginTop:0}}>New Purchase Order {procOrderForm.request_id && <span style={{fontSize:14,color:'#888'}}>(from approved request)</span>}</h3>
 <form onSubmit={submitProcOrder}>
 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16}}>
 <div><label className="form-label">Vendor Name *</label><input className="form-input" required value={procOrderForm.vendor_name} onChange={e=>setProcOrderForm(p=>({...p,vendor_name:e.target.value}))}/></div>
 <div><label className="form-label">Vendor Contact</label><input className="form-input" value={procOrderForm.vendor_contact} onChange={e=>setProcOrderForm(p=>({...p,vendor_contact:e.target.value}))}/></div>
 <div><label className="form-label">Vendor Phone</label><input className="form-input" value={procOrderForm.vendor_phone} onChange={e=>setProcOrderForm(p=>({...p,vendor_phone:e.target.value}))}/></div>
 <div><label className="form-label">Vendor Email</label><input className="form-input" value={procOrderForm.vendor_email} onChange={e=>setProcOrderForm(p=>({...p,vendor_email:e.target.value}))}/></div>
 <div style={{gridColumn:'1/-1'}}><label className="form-label">Vendor Address</label><textarea className="form-input" rows={2} value={procOrderForm.vendor_address} onChange={e=>setProcOrderForm(p=>({...p,vendor_address:e.target.value}))}/></div>
 <div><label className="form-label">Expected Delivery</label><input className="form-input" type="date" value={procOrderForm.expected_delivery} onChange={e=>setProcOrderForm(p=>({...p,expected_delivery:e.target.value}))}/></div>
 <div><label className="form-label">Created By</label><input className="form-input" value={procOrderForm.created_by} onChange={e=>setProcOrderForm(p=>({...p,created_by:e.target.value}))}/></div>
 <div><label className="form-label">Tax Amount (NGN)</label><input className="form-input" type="number" step="0.01" value={procOrderForm.tax_amount} onChange={e=>setProcOrderForm(p=>({...p,tax_amount:e.target.value}))}/></div>
 <div><label className="form-label">Shipping Cost (NGN)</label><input className="form-input" type="number" step="0.01" value={procOrderForm.shipping_cost} onChange={e=>setProcOrderForm(p=>({...p,shipping_cost:e.target.value}))}/></div>
 </div>

 <h4 style={{marginTop:20}}>Order Items</h4>
 {procOrderForm.items.map((item, idx) => (
 <div key={idx} style={{display:'grid',gridTemplateColumns:'2fr 1fr 1fr 1fr 60px',gap:8,marginBottom:8,alignItems:'end'}}>
 <div><label className="form-label" style={{fontSize:11}}>Item Name *</label><input className="form-input" required value={item.item_name} onChange={e=>{const items=[...procOrderForm.items];items[idx].item_name=e.target.value;setProcOrderForm(p=>({...p,items}));}}/></div>
 <div><label className="form-label" style={{fontSize:11}}>Qty</label><input className="form-input" type="number" min="1" value={item.quantity} onChange={e=>{const items=[...procOrderForm.items];items[idx].quantity=e.target.value;setProcOrderForm(p=>({...p,items}));}}/></div>
 <div><label className="form-label" style={{fontSize:11}}>Unit</label><input className="form-input" value={item.unit} onChange={e=>{const items=[...procOrderForm.items];items[idx].unit=e.target.value;setProcOrderForm(p=>({...p,items}));}}/></div>
 <div><label className="form-label" style={{fontSize:11}}>Unit Cost (NGN)</label><input className="form-input" type="number" step="0.01" value={item.unit_cost} onChange={e=>{const items=[...procOrderForm.items];items[idx].unit_cost=e.target.value;setProcOrderForm(p=>({...p,items}));}}/></div>
 <button type="button" className="btn btn-danger" style={{padding:'6px 10px',fontSize:12}} onClick={()=>{const items=procOrderForm.items.filter((_,i)=>i!==idx);setProcOrderForm(p=>({...p,items:items.length?items:[{item_type:'general',item_name:'',quantity:'1',unit:'each',unit_cost:'',specification:''}]}));}}>X</button>
 </div>
 ))}
 <button type="button" className="btn btn-secondary" style={{fontSize:12,marginBottom:16}} onClick={()=>setProcOrderForm(p=>({...p,items:[...p.items,{item_type:'general',item_name:'',quantity:'1',unit:'each',unit_cost:'',specification:''}]}))}>+ Add Item</button>

 <div style={{marginTop:8,padding:12,background:'#f8f9fa',borderRadius:8}}>
 <strong>Subtotal: </strong>{formatCurrency(procOrderForm.items.reduce((s,i)=>s+(parseFloat(i.quantity||0)*parseFloat(i.unit_cost||0)),0))}
 <strong style={{marginLeft:20}}>+ Tax: </strong>{formatCurrency(procOrderForm.tax_amount||0)}
 <strong style={{marginLeft:20}}>+ Shipping: </strong>{formatCurrency(procOrderForm.shipping_cost||0)}
 <strong style={{marginLeft:20}}>= Total: </strong>{formatCurrency(procOrderForm.items.reduce((s,i)=>s+(parseFloat(i.quantity||0)*parseFloat(i.unit_cost||0)),0)+parseFloat(procOrderForm.tax_amount||0)+parseFloat(procOrderForm.shipping_cost||0))}
 </div>

 <div><label className="form-label">Notes</label><textarea className="form-input" rows={2} value={procOrderForm.notes} onChange={e=>setProcOrderForm(p=>({...p,notes:e.target.value}))}/></div>
 <div style={{marginTop:16,display:'flex',gap:8}}>
 <button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Creating...':'Create Purchase Order'}</button>
 <button type="button" className="btn btn-secondary" onClick={()=>setProcView('dashboard')}>Cancel</button>
 </div>
 </form>
 </div>
 )}

 {/* Purchase Orders List */}
 {procView === 'orders' && !procSelectedOrder && (
 <div style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h3 style={{marginTop:0}}>Purchase Orders</h3>
 <table className="data-table">
 <thead><tr><th>PO #</th><th>Vendor</th><th>Status</th><th>Total</th><th>Paid</th><th>Payment</th><th>Order Date</th><th>Actions</th></tr></thead>
 <tbody>
 {procOrders.map(o => (
 <tr key={o.id}>
 <td><button className="btn-link" onClick={()=>fetchProcOrderDetail(o.id)}>{o.po_number}</button></td>
 <td>{o.vendor_name}</td>
 <td><span style={{padding:'2px 10px',borderRadius:12,fontSize:11,fontWeight:600,background:o.status==='ordered'?'#3498db':o.status==='received'?'#27ae60':o.status==='draft'?'#95a5a6':'#f39c12',color:'#fff'}}>{o.status.toUpperCase()}</span></td>
 <td>{formatCurrency(o.total_amount)}</td>
 <td>{formatCurrency(o.paid_amount)}</td>
 <td><span style={{padding:'2px 10px',borderRadius:12,fontSize:11,fontWeight:600,background:o.payment_status==='paid'?'#27ae60':o.payment_status==='partial'?'#f39c12':'#e74c3c',color:'#fff'}}>{(o.payment_status||'unpaid').toUpperCase()}</span></td>
 <td>{o.order_date?new Date(o.order_date).toLocaleDateString():''}</td>
 <td style={{display:'flex',gap:4}}>
 {o.status==='ordered' && <button className="btn btn-primary" style={{fontSize:11,padding:'4px 8px'}} onClick={()=>receiveProcOrder(o.id)}>Receive</button>}
 </td>
 </tr>
 ))}
 {procOrders.length===0 && <tr><td colSpan="8" style={{textAlign:'center',padding:32,color:'#888'}}>No purchase orders found</td></tr>}
 </tbody>
 </table>
 </div>
 )}

 {/* PO Detail */}
 {procView==='orders' && procSelectedOrder && (
 <div style={{background:'#fff',padding:24,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <button className="btn btn-secondary" style={{marginBottom:16}} onClick={()=>setProcSelectedOrder(null)}>Back to List</button>
 <h3 style={{marginTop:0}}>PO: {procSelectedOrder.po_number}</h3>
 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:16,marginBottom:20}}>
 <div><strong>Vendor:</strong> {procSelectedOrder.vendor_name}</div>
 <div><strong>Contact:</strong> {procSelectedOrder.vendor_contact||'N/A'}</div>
 <div><strong>Phone:</strong> {procSelectedOrder.vendor_phone||'N/A'}</div>
 <div><strong>Status:</strong> <span style={{padding:'2px 10px',borderRadius:12,fontSize:11,fontWeight:600,background:procSelectedOrder.status==='ordered'?'#3498db':procSelectedOrder.status==='received'?'#27ae60':'#95a5a6',color:'#fff'}}>{procSelectedOrder.status.toUpperCase()}</span></div>
 <div><strong>Payment:</strong> <span style={{padding:'2px 10px',borderRadius:12,fontSize:11,fontWeight:600,background:procSelectedOrder.payment_status==='paid'?'#27ae60':procSelectedOrder.payment_status==='partial'?'#f39c12':'#e74c3c',color:'#fff'}}>{(procSelectedOrder.payment_status||'unpaid').toUpperCase()}</span></div>
 <div><strong>Expected Delivery:</strong> {procSelectedOrder.expected_delivery||'N/A'}</div>
 </div>

 <div style={{display:'grid',gridTemplateColumns:'repeat(4,1fr)',gap:12,marginBottom:20,textAlign:'center'}}>
 <div style={{padding:16,background:'#f8f9fa',borderRadius:8}}><div style={{fontSize:11,color:'#888'}}>Subtotal</div><div style={{fontSize:18,fontWeight:700}}>{formatCurrency(procSelectedOrder.subtotal)}</div></div>
 <div style={{padding:16,background:'#f8f9fa',borderRadius:8}}><div style={{fontSize:11,color:'#888'}}>Tax</div><div style={{fontSize:18,fontWeight:700}}>{formatCurrency(procSelectedOrder.tax_amount)}</div></div>
 <div style={{padding:16,background:'#f8f9fa',borderRadius:8}}><div style={{fontSize:11,color:'#888'}}>Shipping</div><div style={{fontSize:18,fontWeight:700}}>{formatCurrency(procSelectedOrder.shipping_cost)}</div></div>
 <div style={{padding:16,background:'#e8f5e9',borderRadius:8}}><div style={{fontSize:11,color:'#888'}}>Total</div><div style={{fontSize:18,fontWeight:700,color:'#2c3e50'}}>{formatCurrency(procSelectedOrder.total_amount)}</div></div>
 </div>
 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12,marginBottom:20,textAlign:'center'}}>
 <div style={{padding:16,background:'#e8f5e9',borderRadius:8}}><div style={{fontSize:11,color:'#888'}}>Paid</div><div style={{fontSize:20,fontWeight:700,color:'#27ae60'}}>{formatCurrency(procSelectedOrder.paid_amount)}</div></div>
 <div style={{padding:16,background:'#fbe9e7',borderRadius:8}}><div style={{fontSize:11,color:'#888'}}>Balance</div><div style={{fontSize:20,fontWeight:700,color:'#e74c3c'}}>{formatCurrency((procSelectedOrder.total_amount||0)-(procSelectedOrder.paid_amount||0))}</div></div>
 </div>

 <h4>Items ({procSelectedOrder.items?.length || 0})</h4>
 <table className="data-table"><thead><tr><th>Item</th><th>Type</th><th>Qty</th><th>Unit</th><th>Unit Cost</th><th>Line Total</th></tr></thead>
 <tbody>{(procSelectedOrder.items||[]).map((it,i)=>(
 <tr key={i}><td>{it.item_name}</td><td style={{textTransform:'capitalize'}}>{it.item_type}</td><td>{it.quantity}</td><td>{it.unit}</td><td>{formatCurrency(it.unit_cost)}</td><td>{formatCurrency(it.line_total)}</td></tr>
 ))}</tbody></table>

 {/* Payment form */}
 {procSelectedOrder.payment_status !== 'paid' && (
 <div style={{marginTop:20,padding:16,background:'#f8f9fa',borderRadius:8}}>
 <h4 style={{marginTop:0}}>Record Payment</h4>
 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr 80px',gap:8,alignItems:'end'}}>
 <div><label className="form-label" style={{fontSize:11}}>Amount (NGN)</label><input className="form-input" type="number" step="0.01" value={procPoPayForm.amount} onChange={e=>setProcPoPayForm(p=>({...p,amount:e.target.value}))}/></div>
 <div><label className="form-label" style={{fontSize:11}}>Method</label><select className="form-input" value={procPoPayForm.payment_method} onChange={e=>setProcPoPayForm(p=>({...p,payment_method:e.target.value}))}>
 <option value="">Select...</option><option value="bank_transfer">Bank Transfer</option><option value="cash">Cash</option><option value="cheque">Cheque</option><option value="pos">POS</option>
 </select></div>
 <div><label className="form-label" style={{fontSize:11}}>Reference</label><input className="form-input" value={procPoPayForm.payment_reference} onChange={e=>setProcPoPayForm(p=>({...p,payment_reference:e.target.value}))}/></div>
 <button className="btn btn-primary" style={{padding:'8px 12px'}} disabled={loading} onClick={()=>payProcOrder(procSelectedOrder.id)}>{loading?'...':'Pay'}</button>
 </div>
 </div>
 )}

 {procSelectedOrder.status==='ordered' && (
 <button className="btn btn-primary" style={{marginTop:16}} onClick={()=>receiveProcOrder(procSelectedOrder.id)}>Mark as Received</button>
 )}
 </div>
 )}

 {/* Purchase Invoices */}
 {procView === 'invoices' && (
 <div style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h3 style={{marginTop:0}}>Purchase Invoices</h3>
 <table className="data-table">
 <thead><tr><th>Invoice #</th><th>PO #</th><th>Vendor</th><th>Category</th><th>Total</th><th>Paid</th><th>Status</th><th>Date</th></tr></thead>
 <tbody>
 {procInvoices.map(inv => (
 <tr key={inv.id}>
 <td>{inv.invoice_number}</td><td>{inv.po_number||'N/A'}</td><td>{inv.vendor_name}</td>
 <td style={{textTransform:'capitalize'}}>{inv.category}</td>
 <td>{formatCurrency(inv.total_amount)}</td><td>{formatCurrency(inv.paid_amount)}</td>
 <td><span style={{padding:'2px 10px',borderRadius:12,fontSize:11,fontWeight:600,background:inv.payment_status==='paid'?'#27ae60':inv.payment_status==='partial'?'#f39c12':'#e74c3c',color:'#fff'}}>{(inv.payment_status||'unpaid').toUpperCase()}</span></td>
 <td>{inv.invoice_date}</td>
 </tr>
 ))}
 {procInvoices.length===0 && <tr><td colSpan="8" style={{textAlign:'center',padding:32,color:'#888'}}>No purchase invoices found</td></tr>}
 </tbody>
 </table>
 </div>
 )}

 {/* Expenses List */}
 {procView === 'expenses' && (
 <div style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h3 style={{marginTop:0}}>Expense Records</h3>
 <div style={{display:'flex',gap:8,marginBottom:16,flexWrap:'wrap'}}>
 {['all','procurement','salary','wages','logistics','operational','general'].map(c => (
 <button key={c} className={`btn btn-secondary`} style={{fontSize:11,padding:'4px 10px'}} onClick={()=>c==='all'?fetchProcExpenses():fetchProcExpenses(c)}>{c.toUpperCase()}</button>
 ))}
 </div>
 <table className="data-table">
 <thead><tr><th>Expense #</th><th>Category</th><th>Description</th><th>Amount</th><th>Recipient</th><th>Method</th><th>Date</th></tr></thead>
 <tbody>
 {procExpenses.map(exp => (
 <tr key={exp.id}>
 <td>{exp.expense_number}</td>
 <td style={{textTransform:'capitalize'}}>{exp.category}</td>
 <td>{exp.description}</td>
 <td style={{fontWeight:600}}>{formatCurrency(exp.amount)}</td>
 <td>{exp.recipient||'N/A'}</td>
 <td style={{textTransform:'capitalize'}}>{(exp.payment_method||'').replace('_',' ')}</td>
 <td>{exp.payment_date}</td>
 </tr>
 ))}
 {procExpenses.length===0 && <tr><td colSpan="7" style={{textAlign:'center',padding:32,color:'#888'}}>No expense records found</td></tr>}
 </tbody>
 </table>
 </div>
 )}

 {/* Record Expense Form */}
 {procView === 'newExpense' && (
 <div style={{background:'#fff',padding:24,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h3 style={{marginTop:0}}>Record Expense</h3>
 <form onSubmit={submitProcExpense}>
 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:16}}>
 <div><label className="form-label">Category *</label>
 <select className="form-input" required value={procExpenseForm.category} onChange={e=>setProcExpenseForm(p=>({...p,category:e.target.value}))}>
 <option value="general">General</option><option value="procurement">Procurement</option><option value="salary">Salary</option>
 <option value="wages">Wages</option><option value="logistics">Logistics</option><option value="operational">Operational</option>
 <option value="utilities">Utilities</option><option value="maintenance">Maintenance</option>
 </select>
 </div>
 <div><label className="form-label">Subcategory</label><input className="form-input" value={procExpenseForm.subcategory} onChange={e=>setProcExpenseForm(p=>({...p,subcategory:e.target.value}))}/></div>
 <div style={{gridColumn:'1/-1'}}><label className="form-label">Description *</label><textarea className="form-input" rows={2} required value={procExpenseForm.description} onChange={e=>setProcExpenseForm(p=>({...p,description:e.target.value}))}/></div>
 <div><label className="form-label">Amount (NGN) *</label><input className="form-input" type="number" step="0.01" required value={procExpenseForm.amount} onChange={e=>setProcExpenseForm(p=>({...p,amount:e.target.value}))}/></div>
 <div><label className="form-label">Payment Method</label>
 <select className="form-input" value={procExpenseForm.payment_method} onChange={e=>setProcExpenseForm(p=>({...p,payment_method:e.target.value}))}>
 <option value="">Select...</option><option value="bank_transfer">Bank Transfer</option><option value="cash">Cash</option><option value="cheque">Cheque</option><option value="pos">POS</option>
 </select>
 </div>
 <div><label className="form-label">Payment Reference</label><input className="form-input" value={procExpenseForm.payment_reference} onChange={e=>setProcExpenseForm(p=>({...p,payment_reference:e.target.value}))}/></div>
 <div><label className="form-label">Payment Date</label><input className="form-input" type="date" value={procExpenseForm.payment_date} onChange={e=>setProcExpenseForm(p=>({...p,payment_date:e.target.value}))}/></div>
 <div><label className="form-label">Recipient</label><input className="form-input" value={procExpenseForm.recipient} onChange={e=>setProcExpenseForm(p=>({...p,recipient:e.target.value}))}/></div>
 <div><label className="form-label">Approved By</label><input className="form-input" value={procExpenseForm.approved_by} onChange={e=>setProcExpenseForm(p=>({...p,approved_by:e.target.value}))}/></div>
 <div style={{gridColumn:'1/-1'}}><label className="form-label">Notes</label><textarea className="form-input" rows={2} value={procExpenseForm.notes} onChange={e=>setProcExpenseForm(p=>({...p,notes:e.target.value}))}/></div>
 </div>
 <div style={{marginTop:16,display:'flex',gap:8}}>
 <button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Recording...':'Record Expense'}</button>
 <button type="button" className="btn btn-secondary" onClick={()=>setProcView('expenses')}>Cancel</button>
 </div>
 </form>
 </div>
 )}
 </div>
 )}

 {/* ===================== LOGISTICS MODULE ===================== */}
 {activeModule === 'logistics' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2>Logistics & Delivery</h2>
 </div>
 <div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
 {['dashboard','createManifest','manifests','analytics'].map(v => (
 <button key={v} className={`btn ${logView===v?'btn-primary':'btn-secondary'}`} style={{fontSize:12,padding:'6px 12px'}} onClick={()=>{setLogView(v);setLogSelectedManifest(null);}}>
 {v==='dashboard'?'Dashboard':v==='createManifest'?'Create Manifest':v==='manifests'?'All Manifests':'Analytics'}
 </button>
 ))}
 </div>
 </div>

 {/* Logistics Dashboard */}
 {logView === 'dashboard' && logDashboard && (
 <div>
 <div className="stats-grid" style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(180px,1fr))',gap:16,marginBottom:24}}>
 <div style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h4 style={{margin:'0 0 8px',color:'#888',fontSize:13}}>Total Manifests</h4>
 <div style={{fontSize:28,fontWeight:700,color:'#3498db'}}>{logDashboard.summary?.total_manifests || 0}</div>
 </div>
 <div style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h4 style={{margin:'0 0 8px',color:'#888',fontSize:13}}>Completed</h4>
 <div style={{fontSize:28,fontWeight:700,color:'#27ae60'}}>{logDashboard.summary?.completed || 0}</div>
 </div>
 <div style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h4 style={{margin:'0 0 8px',color:'#888',fontSize:13}}>Preparing / In Transit</h4>
 <div style={{fontSize:28,fontWeight:700,color:'#f39c12'}}>{(logDashboard.summary?.preparing||0)+(logDashboard.summary?.in_transit||0)}</div>
 </div>
 <div style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h4 style={{margin:'0 0 8px',color:'#888',fontSize:13}}>Total Logistics Cost</h4>
 <div style={{fontSize:22,fontWeight:700,color:'#2c3e50'}}>{formatCurrency(logDashboard.summary?.total_logistics_cost || 0)}</div>
 </div>
 <div style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h4 style={{margin:'0 0 8px',color:'#888',fontSize:13}}>Avg. Manifest Cost</h4>
 <div style={{fontSize:22,fontWeight:700,color:'#8e44ad'}}>{formatCurrency(logDashboard.summary?.avg_manifest_cost || 0)}</div>
 </div>
 <div style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h4 style={{margin:'0 0 8px',color:'#888',fontSize:13}}>Customer Drops Delivered</h4>
 <div style={{fontSize:28,fontWeight:700,color:'#16a085'}}>{logDashboard.delivery_stats?.delivered_drops || 0} / {logDashboard.delivery_stats?.total_customer_drops || 0}</div>
 </div>
 </div>

 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:20,marginBottom:20}}>
 {logDashboard.by_officer?.length > 0 && (
 <div style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h3 style={{marginTop:0}}>By Logistics Officer</h3>
 <table className="data-table"><thead><tr><th>Officer</th><th>Manifests</th><th>Total Cost</th></tr></thead>
 <tbody>{logDashboard.by_officer.map((o,i) => (
 <tr key={i}><td>{o.officer}</td><td>{o.manifests}</td><td>{formatCurrency(o.total_cost)}</td></tr>
 ))}</tbody></table>
 </div>
 )}
 {logDashboard.monthly_trend?.length > 0 && (
 <div style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h3 style={{marginTop:0}}>Monthly Trend</h3>
 <table className="data-table"><thead><tr><th>Month</th><th>Manifests</th><th>Total Cost</th></tr></thead>
 <tbody>{logDashboard.monthly_trend.map((m,i) => (
 <tr key={i}><td>{m.month}</td><td>{m.manifests}</td><td>{formatCurrency(m.total_cost)}</td></tr>
 ))}</tbody></table>
 </div>
 )}
 </div>

 {logDashboard.recent_manifests?.length > 0 && (
 <div style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h3 style={{marginTop:0}}>Recent Manifests</h3>
 <table className="data-table"><thead><tr><th>Manifest #</th><th>Officer</th><th>Date</th><th>Customers</th><th>Cost</th><th>Status</th></tr></thead>
 <tbody>{logDashboard.recent_manifests.map((d,i) => (
 <tr key={i} style={{cursor:'pointer'}} onClick={()=>{fetchLogManifestDetail(d.id);setLogView('manifests');}}>
 <td style={{color:'#667eea',fontWeight:600}}>{d.manifest_number}</td><td>{d.logistics_officer}</td>
 <td>{d.delivery_date}</td><td>{d.customer_count}</td>
 <td>{formatCurrency(d.total_cost)}</td>
 <td><span style={{padding:'2px 10px',borderRadius:12,fontSize:11,fontWeight:600,background:d.status==='completed'?'#27ae60':d.status==='in_transit'||d.status==='dispatched'?'#3498db':d.status==='preparing'?'#f39c12':'#95a5a6',color:'#fff'}}>{d.status.replace('_',' ').toUpperCase()}</span></td>
 </tr>
 ))}</tbody></table>
 </div>
 )}
 </div>
 )}

 {/* Create Manifest Form */}
 {logView === 'createManifest' && (
 <div style={{background:'#fff',padding:24,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h3 style={{marginTop:0}}>Create Delivery Manifest</h3>
 <p style={{color:'#666',fontSize:13,marginBottom:16}}>Create a batch delivery manifest. Add all customers and their items that will be delivered in this trip.</p>
 <form onSubmit={submitLogManifest}>
 <div style={{background:'#f8f9fa',padding:16,borderRadius:8,marginBottom:20}}>
 <h4 style={{marginTop:0,color:'#667eea'}}>Trip Details</h4>
 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:16}}>
 <div><label className="form-label">Logistics Officer *</label>
 <select className="form-input" required value={logManifestForm.logistics_officer} onChange={e=>setLogManifestForm(p=>({...p,logistics_officer:e.target.value}))}>
 <option value="">-- Select Logistics Officer --</option>
 {(data.staff||[]).map(s=>(<option key={s.id} value={`${s.first_name} ${s.last_name}`}>{s.first_name} {s.last_name} ({s.employee_id})</option>))}
 </select>
 </div>
 <div><label className="form-label">Delivery Date *</label><input className="form-input" type="date" required value={logManifestForm.delivery_date} onChange={e=>setLogManifestForm(p=>({...p,delivery_date:e.target.value}))}/></div>
 <div><label className="form-label">Transport Mode</label>
 <select className="form-input" value={logManifestForm.transport_mode} onChange={e=>setLogManifestForm(p=>({...p,transport_mode:e.target.value}))}>
 <option value="vehicle">Vehicle</option><option value="motorcycle">Motorcycle</option><option value="truck">Truck</option><option value="bus">Bus</option><option value="courier">Courier</option>
 </select>
 </div>
 <div><label className="form-label">Vehicle Details</label><input className="form-input" placeholder="e.g. Toyota Hilux - ABC 123" value={logManifestForm.vehicle_details} onChange={e=>setLogManifestForm(p=>({...p,vehicle_details:e.target.value}))}/></div>
 <div><label className="form-label">Driver Name</label><input className="form-input" value={logManifestForm.driver_name} onChange={e=>setLogManifestForm(p=>({...p,driver_name:e.target.value}))}/></div>
 <div><label className="form-label">Driver Phone</label><input className="form-input" value={logManifestForm.driver_phone} onChange={e=>setLogManifestForm(p=>({...p,driver_phone:e.target.value}))}/></div>
 <div><label className="form-label">Transport Cost (NGN)</label><input className="form-input" type="number" step="0.01" value={logManifestForm.transport_cost} onChange={e=>setLogManifestForm(p=>({...p,transport_cost:e.target.value}))}/></div>
 <div><label className="form-label">Additional Charges (NGN)</label><input className="form-input" type="number" step="0.01" value={logManifestForm.additional_charges} onChange={e=>setLogManifestForm(p=>({...p,additional_charges:e.target.value}))}/></div>
 <div style={{display:'flex',alignItems:'end'}}><div style={{padding:'10px 16px',background:'#e8f5e9',borderRadius:8,fontWeight:700,fontSize:16}}>Total: {formatCurrency(parseFloat(logManifestForm.transport_cost||0)+parseFloat(logManifestForm.additional_charges||0))}</div></div>
 </div>
 <div style={{marginTop:12}}><label className="form-label">Notes</label><textarea className="form-input" rows={2} value={logManifestForm.notes} onChange={e=>setLogManifestForm(p=>({...p,notes:e.target.value}))}/></div>
 </div>

 <h4 style={{marginBottom:8,color:'#667eea'}}>Customers & Items ({logManifestForm.customers.length})</h4>
 {logManifestForm.customers.map((cust, ci) => (
 <div key={ci} style={{border:'2px solid #667eea',borderRadius:10,padding:16,marginBottom:16,background:'#fafbff'}}>
 <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:12}}>
 <h4 style={{margin:0,color:'#667eea'}}>Customer {ci+1}</h4>
 {logManifestForm.customers.length > 1 && (
 <button type="button" className="btn btn-danger" style={{fontSize:11,padding:'4px 10px'}} onClick={()=>{
 const custs = logManifestForm.customers.filter((_,i) => i !== ci);
 setLogManifestForm(p => ({...p, customers: custs}));
 }}>Remove Customer</button>
 )}
 </div>
 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12}}>
 <div><label className="form-label" style={{fontSize:12}}>Customer Name *</label>
 <select className="form-input" required value={cust.customer_id||''} onChange={e=>{
 const custs=[...logManifestForm.customers];
 const selectedCustomer=(data.customers||[]).find(c=>c.id===e.target.value);
 if(selectedCustomer){
 custs[ci]={...custs[ci], customer_id:selectedCustomer.id, customer_name:selectedCustomer.name, customer_phone:selectedCustomer.phone||'', delivery_address:selectedCustomer.address||'', city:selectedCustomer.city||'', state:selectedCustomer.state||''};
 } else {
 custs[ci]={...custs[ci], customer_id:'', customer_name:'', customer_phone:'', delivery_address:'', city:'', state:''};
 }
 setLogManifestForm(p=>({...p,customers:custs}));
 }}>
 <option value="">-- Select Customer --</option>
 {(data.customers||[]).map(c=>(<option key={c.id} value={c.id}>{c.name}{c.phone ? ` (${c.phone})` : ''}</option>))}
 </select>
 </div>
 <div><label className="form-label" style={{fontSize:12}}>Phone</label><input className="form-input" value={cust.customer_phone} onChange={e=>{const custs=[...logManifestForm.customers];custs[ci].customer_phone=e.target.value;setLogManifestForm(p=>({...p,customers:custs}));}}/></div>
 <div style={{gridColumn:'1/-1'}}><label className="form-label" style={{fontSize:12}}>Delivery Address *</label><input className="form-input" required value={cust.delivery_address} onChange={e=>{const custs=[...logManifestForm.customers];custs[ci].delivery_address=e.target.value;setLogManifestForm(p=>({...p,customers:custs}));}}/></div>
 <div><label className="form-label" style={{fontSize:12}}>City</label><input className="form-input" value={cust.city} onChange={e=>{const custs=[...logManifestForm.customers];custs[ci].city=e.target.value;setLogManifestForm(p=>({...p,customers:custs}));}}/></div>
 <div><label className="form-label" style={{fontSize:12}}>State</label><input className="form-input" value={cust.state} onChange={e=>{const custs=[...logManifestForm.customers];custs[ci].state=e.target.value;setLogManifestForm(p=>({...p,customers:custs}));}}/></div>
 </div>
 <div style={{marginTop:12}}>
 <label className="form-label" style={{fontSize:12,fontWeight:600}}>Items for this customer:</label>
 {cust.items.map((item, ii) => (
 <div key={ii} style={{display:'grid',gridTemplateColumns:'2fr 1fr 80px 80px 40px',gap:8,marginBottom:6,alignItems:'end'}}>
 <div>
 <select className="form-input" required value={item.product_id||''} onChange={e=>{
 const custs=[...logManifestForm.customers];
 const selectedProduct=(data.products||[]).find(pr=>pr.id===e.target.value);
 if(selectedProduct){
 custs[ci].items[ii]={...custs[ci].items[ii], product_id:selectedProduct.id, product_name:selectedProduct.name, sku:selectedProduct.sku||''};
 } else {
 custs[ci].items[ii]={...custs[ci].items[ii], product_id:'', product_name:'', sku:''};
 }
 setLogManifestForm(p=>({...p,customers:custs}));
 }}>
 <option value="">-- Select Product --</option>
 {(data.products||[]).sort((a,b)=>(a.name||'').localeCompare(b.name||'')).map(pr=>(<option key={pr.id} value={pr.id}>{pr.name}{pr.sku ? ` (${pr.sku})` : ''}</option>))}
 </select>
 </div>
 <div><input className="form-input" placeholder="SKU" readOnly value={item.sku} style={{background:'#f0f0f0'}}/></div>
 <div><input className="form-input" type="number" min="1" placeholder="Qty" value={item.quantity} onChange={e=>{const custs=[...logManifestForm.customers];custs[ci].items[ii].quantity=e.target.value;setLogManifestForm(p=>({...p,customers:custs}));}}/></div>
 <div><input className="form-input" placeholder="Unit" value={item.unit} onChange={e=>{const custs=[...logManifestForm.customers];custs[ci].items[ii].unit=e.target.value;setLogManifestForm(p=>({...p,customers:custs}));}}/></div>
 <button type="button" style={{background:'#e74c3c',color:'#fff',border:'none',borderRadius:6,padding:'6px',cursor:'pointer',fontSize:12}} onClick={()=>{
 const custs=[...logManifestForm.customers];custs[ci].items=custs[ci].items.filter((_,i)=>i!==ii);
 if(custs[ci].items.length===0) custs[ci].items=[{product_id:'',product_name:'',sku:'',quantity:'1',unit:'each'}];
 setLogManifestForm(p=>({...p,customers:custs}));
 }}>X</button>
 </div>
 ))}
 <button type="button" className="btn btn-secondary" style={{fontSize:11,padding:'4px 10px',marginTop:4}} onClick={()=>{
 const custs=[...logManifestForm.customers];custs[ci].items.push({product_id:'',product_name:'',sku:'',quantity:'1',unit:'each'});
 setLogManifestForm(p=>({...p,customers:custs}));
 }}>+ Add Item</button>
 </div>
 </div>
 ))}

 <button type="button" className="btn btn-secondary" style={{marginBottom:20,background:'#667eea',color:'#fff',border:'none'}} onClick={()=>{
 setLogManifestForm(p=>({...p, customers: [...p.customers, {customer_id:'',customer_name:'',customer_phone:'',delivery_address:'',city:'',state:'',items:[{product_id:'',product_name:'',sku:'',quantity:'1',unit:'each'}]}]}));
 }}>+ Add Another Customer</button>

 <div style={{display:'flex',gap:8}}>
 <button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Creating...':'Create Manifest'}</button>
 <button type="button" className="btn btn-secondary" onClick={()=>setLogView('dashboard')}>Cancel</button>
 </div>
 </form>
 </div>
 )}

 {/* All Manifests List */}
 {logView === 'manifests' && !logSelectedManifest && (
 <div style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h3 style={{marginTop:0}}>All Delivery Manifests</h3>
 <table className="data-table">
 <thead><tr><th>Manifest #</th><th>Officer</th><th>Date</th><th>Customers</th><th>Items</th><th>Delivered</th><th>Cost</th><th>Status</th><th>Actions</th></tr></thead>
 <tbody>
 {logManifests.map(m => (
 <tr key={m.id}>
 <td><button className="btn-link" style={{fontWeight:600}} onClick={()=>fetchLogManifestDetail(m.id)}>{m.manifest_number}</button></td>
 <td>{m.logistics_officer}</td>
 <td>{m.delivery_date}</td>
 <td style={{textAlign:'center'}}>{m.customer_count}</td>
 <td style={{textAlign:'center'}}>{m.total_items}</td>
 <td style={{textAlign:'center',fontWeight:600,color:m.delivered_count===m.customer_count?'#27ae60':'#f39c12'}}>{m.delivered_count}/{m.customer_count}</td>
 <td style={{fontWeight:600}}>{formatCurrency(m.total_cost)}</td>
 <td><span style={{padding:'2px 10px',borderRadius:12,fontSize:11,fontWeight:600,background:m.status==='completed'?'#27ae60':m.status==='in_transit'||m.status==='dispatched'?'#3498db':m.status==='preparing'?'#f39c12':'#95a5a6',color:'#fff'}}>{m.status.replace('_',' ').toUpperCase()}</span></td>
 <td style={{display:'flex',gap:4,flexWrap:'wrap'}}>
 <button className="btn btn-secondary" style={{fontSize:11,padding:'3px 8px'}} onClick={()=>downloadManifestPdf(m.id, m.manifest_number)}>PDF</button>
 <button className="btn btn-primary" style={{fontSize:11,padding:'3px 8px',background:'#e67e22',borderColor:'#e67e22'}} onClick={()=>printManifestThermal(m.id, m.manifest_number)}>Print</button>
 {m.status==='preparing' && <button className="btn btn-primary" style={{fontSize:11,padding:'3px 8px'}} onClick={()=>updateManifestStatus(m.id,'dispatched')}>Dispatch</button>}
 {(!currentUser || currentUser.role === 'admin') && (
 <button className="btn btn-danger" style={{fontSize:11,padding:'3px 8px'}} onClick={()=>deleteManifest(m.id, m.manifest_number)}>Delete</button>
 )}
 </td>
 </tr>
 ))}
 {logManifests.length===0 && <tr><td colSpan="9" style={{textAlign:'center',padding:32,color:'#888'}}>No manifests found</td></tr>}
 </tbody>
 </table>
 </div>
 )}

 {/* Manifest Detail */}
 {logView === 'manifests' && logSelectedManifest && (
 <div style={{background:'#fff',padding:24,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <div style={{display:'flex',gap:8,marginBottom:16,flexWrap:'wrap'}}>
 <button className="btn btn-secondary" onClick={()=>setLogSelectedManifest(null)}>Back to List</button>
 <button className="btn btn-primary" style={{fontSize:12}} onClick={()=>downloadManifestPdf(logSelectedManifest.id, logSelectedManifest.manifest_number)}>Download PDF</button>
 <button className="btn btn-primary" style={{fontSize:12,background:'#e67e22',borderColor:'#e67e22'}} onClick={()=>printManifestThermal(logSelectedManifest.id, logSelectedManifest.manifest_number)}>Print Manifest</button>
 {logSelectedManifest.status==='preparing' && <button className="btn btn-primary" style={{background:'#27ae60',borderColor:'#27ae60',fontSize:12}} onClick={()=>updateManifestStatus(logSelectedManifest.id,'dispatched')}>Mark Dispatched</button>}
 {logSelectedManifest.status==='dispatched' && <button className="btn btn-primary" style={{background:'#3498db',borderColor:'#3498db',fontSize:12}} onClick={()=>updateManifestStatus(logSelectedManifest.id,'in_transit')}>Mark In Transit</button>}
 {(!currentUser || currentUser.role === 'admin') && (
 <button className="btn btn-danger" style={{fontSize:12}} onClick={()=>deleteManifest(logSelectedManifest.id, logSelectedManifest.manifest_number)}>Delete Manifest</button>
 )}
 </div>
 <h3 style={{marginTop:0}}>Manifest: {logSelectedManifest.manifest_number}</h3>
 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:16,marginBottom:20}}>
 <div><strong>Logistics Officer:</strong> {logSelectedManifest.logistics_officer}</div>
 <div><strong>Delivery Date:</strong> {logSelectedManifest.delivery_date}</div>
 <div><strong>Transport Mode:</strong> {(logSelectedManifest.transport_mode||'vehicle').replace('_',' ')}</div>
 <div><strong>Vehicle:</strong> {logSelectedManifest.vehicle_details||'N/A'}</div>
 <div><strong>Driver:</strong> {logSelectedManifest.driver_name||'N/A'} {logSelectedManifest.driver_phone?`(${logSelectedManifest.driver_phone})`:''}</div>
 <div><strong>Status:</strong> <span style={{padding:'2px 10px',borderRadius:12,fontSize:11,fontWeight:600,background:logSelectedManifest.status==='completed'?'#27ae60':logSelectedManifest.status==='in_transit'||logSelectedManifest.status==='dispatched'?'#3498db':'#f39c12',color:'#fff'}}>{logSelectedManifest.status.replace('_',' ').toUpperCase()}</span></div>
 </div>

 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:12,marginBottom:24,textAlign:'center'}}>
 <div style={{padding:16,background:'#f8f9fa',borderRadius:8}}><div style={{fontSize:11,color:'#888'}}>Transport Cost</div><div style={{fontSize:20,fontWeight:700}}>{formatCurrency(logSelectedManifest.transport_cost)}</div></div>
 <div style={{padding:16,background:'#f8f9fa',borderRadius:8}}><div style={{fontSize:11,color:'#888'}}>Additional Charges</div><div style={{fontSize:20,fontWeight:700}}>{formatCurrency(logSelectedManifest.additional_charges)}</div></div>
 <div style={{padding:16,background:'#e8f5e9',borderRadius:8}}><div style={{fontSize:11,color:'#888'}}>Total Cost</div><div style={{fontSize:22,fontWeight:700,color:'#2c3e50'}}>{formatCurrency(logSelectedManifest.total_cost)}</div></div>
 </div>

 {logSelectedManifest.notes && <div style={{marginBottom:16,padding:12,background:'#f8f9fa',borderRadius:8}}><strong>Notes:</strong> {logSelectedManifest.notes}</div>}

 <h4 style={{color:'#667eea'}}>Customer Deliveries ({logSelectedManifest.customers?.length || 0})</h4>
 {(logSelectedManifest.customers||[]).map((cust, ci) => (
 <div key={cust.id} style={{border:'1px solid #ddd',borderRadius:10,padding:16,marginBottom:16,background:cust.status==='delivered'?'#f0fff0':'#fffbf0'}}>
 <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:8}}>
 <h4 style={{margin:0}}>
 <span style={{color:cust.status==='delivered'?'#27ae60':'#f39c12',marginRight:8}}>{cust.status==='delivered'?'[DELIVERED]':'[PENDING]'}</span>
 {cust.customer_name}
 </h4>
 {cust.status !== 'delivered' && logSelectedManifest.status !== 'preparing' && (
 <button className="btn btn-primary" style={{fontSize:12,background:'#27ae60',borderColor:'#27ae60'}} onClick={()=>{setLogConfirmingCustomerId(cust.id);setLogConfirmForm({receiver_name:'',receiver_phone:'',physical_invoice_number:'',delivery_notes:'',signature_collected:true});}}>Confirm Delivery</button>
 )}
 </div>
 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:8,fontSize:13,marginBottom:8}}>
 <div><strong>Phone:</strong> {cust.customer_phone||'N/A'}</div>
 <div><strong>Address:</strong> {cust.delivery_address}{cust.city?`, ${cust.city}`:''}{cust.state?`, ${cust.state}`:''}</div>
 {cust.status==='delivered' && <div><strong>Delivered:</strong> {cust.delivery_time?new Date(cust.delivery_time).toLocaleString():'N/A'}</div>}
 </div>
 {cust.status==='delivered' && (
 <div style={{background:'#e8f5e9',padding:10,borderRadius:6,fontSize:13,marginBottom:8}}>
 <strong>Receiver:</strong> {cust.receiver_name} ({cust.receiver_phone||'N/A'}) | <strong>Invoice #:</strong> {cust.physical_invoice_number||'N/A'}
 {cust.delivery_notes && <span> | <strong>Notes:</strong> {cust.delivery_notes}</span>}
 </div>
 )}
 <table className="data-table" style={{fontSize:12}}>
 <thead><tr><th>#</th><th>Product</th><th>SKU</th><th>Qty</th><th>Unit</th></tr></thead>
 <tbody>{(cust.items||[]).map((it,i) => (
 <tr key={it.id}><td>{i+1}</td><td>{it.product_name}</td><td>{it.sku||'-'}</td><td>{it.quantity}</td><td>{it.unit}</td></tr>
 ))}</tbody>
 </table>

 {/* Confirm Delivery Form (inline) */}
 {logConfirmingCustomerId === cust.id && (
 <div style={{marginTop:12,padding:16,background:'#fff',border:'2px solid #27ae60',borderRadius:8}}>
 <h4 style={{margin:'0 0 12px',color:'#27ae60'}}>Confirm Delivery for {cust.customer_name}</h4>
 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12}}>
 <div><label className="form-label" style={{fontSize:12}}>Receiver Name *</label><input className="form-input" required value={logConfirmForm.receiver_name} onChange={e=>setLogConfirmForm(p=>({...p,receiver_name:e.target.value}))}/></div>
 <div><label className="form-label" style={{fontSize:12}}>Receiver Phone</label><input className="form-input" value={logConfirmForm.receiver_phone} onChange={e=>setLogConfirmForm(p=>({...p,receiver_phone:e.target.value}))}/></div>
 <div><label className="form-label" style={{fontSize:12}}>Physical Invoice Number *</label><input className="form-input" required placeholder="From invoice booklet" value={logConfirmForm.physical_invoice_number} onChange={e=>setLogConfirmForm(p=>({...p,physical_invoice_number:e.target.value}))}/></div>
 <div><label className="form-label" style={{fontSize:12}}>Delivery Notes</label><input className="form-input" value={logConfirmForm.delivery_notes} onChange={e=>setLogConfirmForm(p=>({...p,delivery_notes:e.target.value}))}/></div>
 <div style={{display:'flex',alignItems:'center',gap:8}}><input type="checkbox" checked={logConfirmForm.signature_collected} onChange={e=>setLogConfirmForm(p=>({...p,signature_collected:e.target.checked}))}/><label style={{fontSize:13}}>Signature Collected</label></div>
 </div>
 <div style={{marginTop:12,display:'flex',gap:8}}>
 <button className="btn btn-primary" style={{background:'#27ae60',borderColor:'#27ae60'}} disabled={loading||!logConfirmForm.receiver_name} onClick={()=>confirmCustomerDelivery(cust.id)}>{loading?'Confirming...':'Confirm Delivery'}</button>
 <button className="btn btn-secondary" onClick={()=>setLogConfirmingCustomerId(null)}>Cancel</button>
 </div>
 </div>
 )}
 </div>
 ))}
 </div>
 )}

 {/* Analytics */}
 {logView === 'analytics' && logAnalytics && (
 <div>
 {logAnalytics.by_transport_mode?.length > 0 && (
 <div style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)',marginBottom:20}}>
 <h3 style={{marginTop:0}}>By Transport Mode</h3>
 <table className="data-table"><thead><tr><th>Mode</th><th>Manifests</th><th>Total Cost</th><th>Avg Cost</th></tr></thead>
 <tbody>{logAnalytics.by_transport_mode.map((m,i)=>(
 <tr key={i}><td style={{textTransform:'capitalize'}}>{m.mode}</td><td>{m.count}</td><td>{formatCurrency(m.total)}</td><td>{formatCurrency(m.avg_cost)}</td></tr>
 ))}</tbody></table>
 </div>
 )}
 {logAnalytics.by_day_of_week?.length > 0 && (
 <div style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)',marginBottom:20}}>
 <h3 style={{marginTop:0}}>By Day of Week</h3>
 <table className="data-table"><thead><tr><th>Day</th><th>Manifests</th><th>Total Cost</th></tr></thead>
 <tbody>{logAnalytics.by_day_of_week.map((d,i)=>(
 <tr key={i}><td>{d.day}</td><td>{d.count}</td><td>{formatCurrency(d.total)}</td></tr>
 ))}</tbody></table>
 </div>
 )}
 {logAnalytics.top_customers?.length > 0 && (
 <div style={{background:'#fff',padding:20,borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.08)'}}>
 <h3 style={{marginTop:0}}>Top Customers by Delivery Frequency</h3>
 <table className="data-table"><thead><tr><th>Customer</th><th>Manifests</th><th>Total Drops</th></tr></thead>
 <tbody>{logAnalytics.top_customers.map((c,i)=>(
 <tr key={i}><td>{c.customer}</td><td>{c.manifests}</td><td>{c.drops}</td></tr>
 ))}</tbody></table>
 </div>
 )}
 </div>
 )}
 </div>
 )}

 {/* ===================== USER MANAGEMENT MODULE ===================== */}
 {activeModule === 'userManagement' && (() => {
 const users = data.users || [];
 const pendingUsers = users.filter(u => !u.is_active && !u.is_locked);
 const activeUsers = users.filter(u => u.is_active && !u.is_locked);
 const lockedUsers = users.filter(u => u.is_locked);
 const inactiveUsers = users.filter(u => !u.is_active);
 const filteredUsers = users.filter(u => {
 const matchesSearch = !umSearch || u.full_name?.toLowerCase().includes(umSearch.toLowerCase()) || u.email?.toLowerCase().includes(umSearch.toLowerCase()) || u.phone?.includes(umSearch);
 if (umFilter === 'all') return matchesSearch;
 if (umFilter === 'pending') return !u.is_active && !u.is_locked && matchesSearch;
 if (umFilter === 'active') return u.is_active && !u.is_locked && matchesSearch;
 if (umFilter === 'locked') return u.is_locked && matchesSearch;
 if (umFilter === 'inactive') return !u.is_active && matchesSearch;
 return matchesSearch;
 });
 const roleLabels = { admin: 'Administrator', sales_staff: 'Sales Staff', marketer: 'Marketer', customer_care: 'Customer Care', production_staff: 'Production Staff', warehouse_logistics: 'Warehouse/Logistics' };
 const roleColors = { admin: '#e74c3c', sales_staff: '#3498db', marketer: '#9b59b6', customer_care: '#2ecc71', production_staff: '#f39c12', warehouse_logistics: '#e67e22' };

 const handleApprove = async (userId) => {
 try { const r = await authedFetch(`/api/auth/users/${userId}/approve`, {method:'POST'}); const d = await r.json(); if (!r.ok) throw new Error(d.detail||'Failed'); notify(d.message,'success'); fetchData('users'); } catch(e) { notify(e.message,'error'); }
 };
 const handleReject = async (userId, email) => {
 if (!window.confirm(`Permanently reject and delete registration for ${email}?`)) return;
 try { const r = await authedFetch(`/api/auth/users/${userId}/reject`, {method:'POST'}); const d = await r.json(); if (!r.ok) throw new Error(d.detail||'Failed'); notify(d.message,'success'); fetchData('users'); } catch(e) { notify(e.message,'error'); }
 };
 const handleDeactivate = async (userId, email) => {
 if (!window.confirm(`Deactivate user ${email}? They will not be able to login.`)) return;
 try { const r = await authedFetch(`/api/auth/users/${userId}/deactivate`, {method:'POST'}); const d = await r.json(); if (!r.ok) throw new Error(d.detail||'Failed'); notify(d.message,'success'); fetchData('users'); } catch(e) { notify(e.message,'error'); }
 };
 const handleToggleLock = async (userId) => {
 try { const r = await authedFetch(`/api/auth/users/${userId}/toggle-lock`, {method:'POST'}); const d = await r.json(); if (!r.ok) throw new Error(d.detail||'Failed'); notify(d.message,'success'); fetchData('users'); } catch(e) { notify(e.message,'error'); }
 };
 const handleChangeRole = async (userId, newRole) => {
 try { const r = await authedFetch(`/api/auth/users/${userId}/role`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({role:newRole})}); const d = await r.json(); if (!r.ok) throw new Error(d.detail||'Failed'); notify(d.message,'success'); setUmEditingRole(null); fetchData('users'); } catch(e) { notify(e.message,'error'); }
 };
 const handleResetPassword = async (userId, email) => {
 if (!window.confirm(`Reset password for ${email}? A temporary password will be assigned.`)) return;
 try { const r = await authedFetch(`/api/auth/users/${userId}/reset-password`, {method:'POST'}); const d = await r.json(); if (!r.ok) throw new Error(d.detail||'Failed'); notify(d.message,'success'); } catch(e) { notify(e.message,'error'); }
 };

 return (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="" className="module-logo" onError={(e)=>{e.target.style.display='none';}} />
 <h2>User Management & Approval</h2>
 </div>
 <button className="btn btn-refresh" onClick={() => fetchData('users')}>Refresh</button>
 </div>

 {/* Stats Cards */}
 <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit, minmax(180px, 1fr))',gap:16,marginBottom:24}}>
 <div onClick={()=>setUmFilter('all')} style={{background:umFilter==='all'?'#2c3e50':'#fff',color:umFilter==='all'?'#fff':'#2c3e50',borderRadius:12,padding:'20px 16px',boxShadow:'0 2px 8px rgba(0,0,0,.08)',cursor:'pointer',textAlign:'center',transition:'all .2s',border:umFilter==='all'?'2px solid #2c3e50':'2px solid #eee'}}>
 <div style={{fontSize:32,fontWeight:800}}>{users.length}</div>
 <div style={{fontSize:13,fontWeight:600,marginTop:4}}>Total Users</div>
 </div>
 <div onClick={()=>setUmFilter('pending')} style={{background:umFilter==='pending'?'#f39c12':'#fff',color:umFilter==='pending'?'#fff':'#f39c12',borderRadius:12,padding:'20px 16px',boxShadow:'0 2px 8px rgba(0,0,0,.08)',cursor:'pointer',textAlign:'center',transition:'all .2s',border:umFilter==='pending'?'2px solid #f39c12':'2px solid #eee'}}>
 <div style={{fontSize:32,fontWeight:800}}>{pendingUsers.length}</div>
 <div style={{fontSize:13,fontWeight:600,marginTop:4}}>Pending Approval</div>
 </div>
 <div onClick={()=>setUmFilter('active')} style={{background:umFilter==='active'?'#27ae60':'#fff',color:umFilter==='active'?'#fff':'#27ae60',borderRadius:12,padding:'20px 16px',boxShadow:'0 2px 8px rgba(0,0,0,.08)',cursor:'pointer',textAlign:'center',transition:'all .2s',border:umFilter==='active'?'2px solid #27ae60':'2px solid #eee'}}>
 <div style={{fontSize:32,fontWeight:800}}>{activeUsers.length}</div>
 <div style={{fontSize:13,fontWeight:600,marginTop:4}}>Active</div>
 </div>
 <div onClick={()=>setUmFilter('locked')} style={{background:umFilter==='locked'?'#e74c3c':'#fff',color:umFilter==='locked'?'#fff':'#e74c3c',borderRadius:12,padding:'20px 16px',boxShadow:'0 2px 8px rgba(0,0,0,.08)',cursor:'pointer',textAlign:'center',transition:'all .2s',border:umFilter==='locked'?'2px solid #e74c3c':'2px solid #eee'}}>
 <div style={{fontSize:32,fontWeight:800}}>{lockedUsers.length}</div>
 <div style={{fontSize:13,fontWeight:600,marginTop:4}}>Locked</div>
 </div>
 </div>

 {/* Pending Approval Alert */}
 {pendingUsers.length > 0 && (
 <div style={{background:'linear-gradient(135deg,#fff3cd,#ffeaa7)',border:'1px solid #f39c12',borderRadius:10,padding:'16px 20px',marginBottom:20,display:'flex',alignItems:'center',gap:12}}>
 <span style={{fontSize:28}}>!</span>
 <div>
 <strong style={{color:'#856404',fontSize:15}}>{pendingUsers.length} user{pendingUsers.length>1?'s':''} awaiting approval</strong>
 <p style={{margin:'4px 0 0',fontSize:13,color:'#856404'}}>New registrations need admin approval before users can access the system.</p>
 </div>
 </div>
 )}

 {/* Search Bar */}
 <div style={{display:'flex',gap:12,marginBottom:20,flexWrap:'wrap',alignItems:'center'}}>
 <input type="text" placeholder="Search by name, email or phone..." value={umSearch} onChange={(e)=>setUmSearch(e.target.value)} style={{flex:1,minWidth:250,padding:'10px 16px',borderRadius:8,border:'1px solid #ddd',fontSize:14}} />
 <select value={umFilter} onChange={(e)=>setUmFilter(e.target.value)} style={{padding:'10px 16px',borderRadius:8,border:'1px solid #ddd',fontSize:14,background:'#fff'}}>
 <option value="all">All Users ({users.length})</option>
 <option value="pending">Pending ({pendingUsers.length})</option>
 <option value="active">Active ({activeUsers.length})</option>
 <option value="locked">Locked ({lockedUsers.length})</option>
 <option value="inactive">Inactive ({inactiveUsers.length})</option>
 </select>
 </div>

 {/* Users Table */}
 <div style={{background:'#fff',borderRadius:12,boxShadow:'0 2px 8px rgba(0,0,0,.06)',overflow:'hidden'}}>
 <div className="table-responsive">
 <table className="data-table" style={{marginBottom:0}}>
 <thead>
 <tr style={{background:'#f8f9fa'}}>
 <th style={{padding:'14px 12px',fontSize:11,textTransform:'uppercase',letterSpacing:.5,color:'#666',fontWeight:700}}>#</th>
 <th style={{padding:'14px 12px',fontSize:11,textTransform:'uppercase',letterSpacing:.5,color:'#666',fontWeight:700}}>User</th>
 <th style={{padding:'14px 12px',fontSize:11,textTransform:'uppercase',letterSpacing:.5,color:'#666',fontWeight:700}}>Contact</th>
 <th style={{padding:'14px 12px',fontSize:11,textTransform:'uppercase',letterSpacing:.5,color:'#666',fontWeight:700}}>Role</th>
 <th style={{padding:'14px 12px',fontSize:11,textTransform:'uppercase',letterSpacing:.5,color:'#666',fontWeight:700}}>Department</th>
 <th style={{padding:'14px 12px',fontSize:11,textTransform:'uppercase',letterSpacing:.5,color:'#666',fontWeight:700}}>Status</th>
 <th style={{padding:'14px 12px',fontSize:11,textTransform:'uppercase',letterSpacing:.5,color:'#666',fontWeight:700}}>Registered</th>
 <th style={{padding:'14px 12px',fontSize:11,textTransform:'uppercase',letterSpacing:.5,color:'#666',fontWeight:700,minWidth:280}}>Actions</th>
 </tr>
 </thead>
 <tbody>
 {filteredUsers.map((u, idx) => (
 <tr key={u.id} style={{background: !u.is_active ? '#fff8f0' : u.is_locked ? '#fff5f5' : idx%2===0 ? '#fff' : '#fafbfc', borderLeft: !u.is_active && !u.is_locked ? '4px solid #f39c12' : u.is_locked ? '4px solid #e74c3c' : '4px solid #27ae60'}}>
 <td style={{padding:'12px',fontSize:12,color:'#999'}}>{idx+1}</td>
 <td style={{padding:'12px'}}>
 <div style={{display:'flex',alignItems:'center',gap:10}}>
 <div style={{width:38,height:38,borderRadius:'50%',background:roleColors[u.role]||'#95a5a6',color:'#fff',display:'flex',alignItems:'center',justifyContent:'center',fontWeight:700,fontSize:14,flexShrink:0}}>
 {(u.full_name||'?').charAt(0).toUpperCase()}
 </div>
 <div>
 <div style={{fontWeight:600,fontSize:14}}>{u.full_name}</div>
 <div style={{fontSize:11,color:'#888'}}>{u.email}</div>
 </div>
 </div>
 </td>
 <td style={{padding:'12px',fontSize:13}}>{u.phone || <span style={{color:'#ccc'}}>No phone</span>}</td>
 <td style={{padding:'12px'}}>
 {umEditingRole === u.id ? (
 <select defaultValue={u.role} onChange={(e)=>handleChangeRole(u.id, e.target.value)} onBlur={()=>setUmEditingRole(null)} autoFocus style={{padding:'4px 8px',borderRadius:6,border:'2px solid #3498db',fontSize:12,fontWeight:600}}>
 <option value="admin">Administrator</option>
 <option value="sales_staff">Sales Staff</option>
 <option value="marketer">Marketer</option>
 <option value="customer_care">Customer Care</option>
 <option value="production_staff">Production Staff</option>
 <option value="warehouse_logistics">Warehouse/Logistics</option>
 </select>
 ) : (
 <span onClick={()=>setUmEditingRole(u.id)} style={{display:'inline-block',padding:'4px 10px',borderRadius:12,fontSize:11,fontWeight:700,background:roleColors[u.role]||'#95a5a6',color:'#fff',cursor:'pointer',letterSpacing:.3}} title="Click to change role">
 {roleLabels[u.role] || u.role}
 </span>
 )}
 </td>
 <td style={{padding:'12px',fontSize:13,color:'#666'}}>{u.department || '-'}</td>
 <td style={{padding:'12px'}}>
 {u.is_locked ? (
 <span style={{display:'inline-flex',alignItems:'center',gap:4,padding:'4px 10px',borderRadius:12,fontSize:11,fontWeight:700,background:'#fde8e8',color:'#e74c3c'}}>Locked</span>
 ) : u.is_active ? (
 <span style={{display:'inline-flex',alignItems:'center',gap:4,padding:'4px 10px',borderRadius:12,fontSize:11,fontWeight:700,background:'#e8f8ef',color:'#27ae60'}}>Active</span>
 ) : (
 <span style={{display:'inline-flex',alignItems:'center',gap:4,padding:'4px 10px',borderRadius:12,fontSize:11,fontWeight:700,background:'#fef3e2',color:'#f39c12'}}>Pending</span>
 )}
 </td>
 <td style={{padding:'12px',fontSize:12,color:'#888'}}>{u.created_at ? new Date(u.created_at).toLocaleDateString('en-NG',{day:'2-digit',month:'short',year:'numeric'}) : '-'}</td>
 <td style={{padding:'12px'}}>
 <div style={{display:'flex',gap:6,flexWrap:'wrap'}}>
 {/* Pending user: Approve / Reject */}
 {!u.is_active && !u.is_locked && (
 <>
 <button onClick={()=>handleApprove(u.id)} style={{padding:'5px 12px',borderRadius:6,border:'none',background:'#27ae60',color:'#fff',fontSize:11,fontWeight:700,cursor:'pointer'}}>Approve</button>
 <button onClick={()=>handleReject(u.id,u.email)} style={{padding:'5px 12px',borderRadius:6,border:'none',background:'#e74c3c',color:'#fff',fontSize:11,fontWeight:700,cursor:'pointer'}}>Reject</button>
 </>
 )}
 {/* Active user: Deactivate / Lock */}
 {u.is_active && !u.is_locked && (
 <>
 <button onClick={()=>handleDeactivate(u.id,u.email)} style={{padding:'5px 12px',borderRadius:6,border:'none',background:'#e67e22',color:'#fff',fontSize:11,fontWeight:700,cursor:'pointer'}}>Deactivate</button>
 <button onClick={()=>handleToggleLock(u.id)} style={{padding:'5px 12px',borderRadius:6,border:'none',background:'#c0392b',color:'#fff',fontSize:11,fontWeight:700,cursor:'pointer'}}>Lock</button>
 </>
 )}
 {/* Locked user: Unlock */}
 {u.is_locked && (
 <button onClick={()=>handleToggleLock(u.id)} style={{padding:'5px 12px',borderRadius:6,border:'none',background:'#27ae60',color:'#fff',fontSize:11,fontWeight:700,cursor:'pointer'}}>Unlock</button>
 )}
 {/* Inactive (deactivated) user: Re-approve */}
 {!u.is_active && u.is_locked && (
 <button onClick={()=>handleApprove(u.id)} style={{padding:'5px 12px',borderRadius:6,border:'none',background:'#27ae60',color:'#fff',fontSize:11,fontWeight:700,cursor:'pointer'}}>Re-Activate</button>
 )}
 {/* Reset password - always available */}
 <button onClick={()=>handleResetPassword(u.id,u.email)} style={{padding:'5px 12px',borderRadius:6,border:'1px solid #ddd',background:'#fff',color:'#333',fontSize:11,fontWeight:600,cursor:'pointer'}}>Reset Pwd</button>
 </div>
 </td>
 </tr>
 ))}
 {filteredUsers.length === 0 && (
 <tr><td colSpan="8" style={{textAlign:'center',padding:40,color:'#aaa',fontSize:15}}>No users match the current filter</td></tr>
 )}
 </tbody>
 </table>
 </div>
 </div>

 {/* Role Overview */}
 <div style={{marginTop:24}}>
 <h3 style={{fontSize:16,fontWeight:700,color:'#2c3e50',marginBottom:12}}>Users by Role</h3>
 <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit, minmax(160px, 1fr))',gap:12}}>
 {Object.entries(roleLabels).map(([key, label]) => {
 const count = users.filter(u => u.role === key).length;
 return (
 <div key={key} style={{background:'#fff',borderRadius:10,padding:'14px 16px',boxShadow:'0 1px 4px rgba(0,0,0,.06)',borderLeft:`4px solid ${roleColors[key]}`}}>
 <div style={{fontSize:22,fontWeight:800,color:roleColors[key]}}>{count}</div>
 <div style={{fontSize:12,fontWeight:600,color:'#666',marginTop:2}}>{label}</div>
 </div>
 );
 })}
 </div>
 </div>
 </div>
 );
 })()}

 {/* Geomap */}
 {activeModule === 'geomap' && (
 <Geomap />
 )}

 {/* Regulatory Compliance */}
 {activeModule === 'regulatoryCompliance' && (
 <RegulatoryCompliance />
 )}

 {/* Network & WiFi Management */}
 {activeModule === 'networkWifi' && (
 <NetworkManagementDashboard />
 )}

 {/* Settings */}
 {activeModule === 'settings' && (
 <div className="module-content">
 <div className="module-header">
 <div className="module-header-left">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 <h2> Settings</h2>
 </div>
 </div>
 <div className="settings-grid">
 {/* User Management */}
 <div className="settings-card" style={{gridColumn: '1 / -1'}}>
 <h3>User Management & Approval</h3>
 <table className="data-table">
 <thead>
 <tr>
 <th>Full Name</th>
 <th>Email</th>
 <th>Role</th>
 <th>Department</th>
 <th>Phone</th>
 <th>Status</th>
 <th>Actions</th>
 </tr>
 </thead>
 <tbody>
 {(data.users || []).map(user => (
 <tr key={user.id} className={!user.is_active ? 'pending-row' : ''}>
 <td>{user.full_name}</td>
 <td>{user.email}</td>
 <td><span className="badge">{user.role}</span></td>
 <td>{user.department || 'N/A'}</td>
 <td>{user.phone || 'N/A'}</td>
 <td>
 {!user.is_active && <span className="status-badge pending"> Pending</span>}
 {user.is_active && !user.is_locked && <span className="status-badge active"> Active</span>}
 {user.is_locked && <span className="status-badge locked">Locked</span>}
 </td>
 <td className="actions">
 {!user.is_active && (
 <>
 <button 
 onClick={async () => {
 try {
 const res = await authedFetch(`/api/auth/users/${user.id}/approve`, {method: 'POST'});
 if (!res.ok) throw new Error('Failed to approve user');
 const result = await res.json();
 notify(result.message, 'success');
 fetchData('users');
 } catch (e) {
 notify(`Error: ${e.message}`, 'error');
 }
 }}
 className="btn-success"
 style={{marginRight: '5px'}}
 >
 Approve
 </button>
 <button 
 onClick={async () => {
 if (!confirm(`Reject registration for ${user.email}?`)) return;
 try {
 const res = await authedFetch(`/api/auth/users/${user.id}/reject`, {method: 'POST'});
 if (!res.ok) throw new Error('Failed to reject user');
 const result = await res.json();
 notify(result.message, 'success');
 fetchData('users');
 } catch (e) {
 notify(`Error: ${e.message}`, 'error');
 }
 }}
 className="btn-danger"
 >
 Reject
 </button>
 </>
 )}
 {user.is_active && (
 <button 
 onClick={async () => {
 try {
 const res = await authedFetch(`/api/auth/users/${user.id}/toggle-lock`, {method: 'POST'});
 if (!res.ok) throw new Error('Failed to toggle lock');
 const result = await res.json();
 notify(result.message, 'success');
 fetchData('users');
 } catch (e) {
 notify(`Error: ${e.message}`, 'error');
 }
 }}
 className={user.is_locked ? 'btn-success' : 'btn-warning'}
 >
 {user.is_locked ? 'Unlock' : 'Lock'}
 </button>
 )}
 </td>
 </tr>
 ))}
 {(!data.users || data.users.length === 0) && (
 <tr><td colSpan="7" style={{textAlign: 'center', padding: '20px'}}>No users found</td></tr>
 )}
 </tbody>
 </table>
 </div>
 
 {/* Module Access Control - Full Grid */}
 <div className="settings-card" style={{gridColumn: '1 / -1'}}>
 <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16}}>
 <h3>Module Access Control</h3>
 <button className="btn btn-primary" onClick={fetchModuleAccessAll} style={{fontSize:12}}>{maLoading ? 'Loading...' : 'Load / Refresh Access Data'}</button>
 </div>
 <p style={{fontSize:13,color:'#666',marginBottom:16}}>Grant or deny access to each module for individual users. Admins always have full access. Changes save immediately per user.</p>
 {moduleAccessData.length === 0 && !maLoading && (
 <div style={{textAlign:'center',padding:30,color:'#aaa'}}>Click "Load / Refresh Access Data" above to view and manage module access for all users.</div>
 )}
 {moduleAccessData.length > 0 && (
 <div style={{overflowX:'auto'}}>
 <table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}>
 <thead>
 <tr style={{background:'#f1f3f5'}}>
 <th style={{padding:'10px 8px',textAlign:'left',position:'sticky',left:0,background:'#f1f3f5',zIndex:2,minWidth:180,borderBottom:'2px solid #dee2e6'}}>User</th>
 <th style={{padding:'10px 4px',textAlign:'center',fontSize:10,borderBottom:'2px solid #dee2e6',minWidth:30}} title="Select All">All</th>
 {['staff','attendance','products','rawMaterials','stockManagement','production','productionTasks','productionCompletions','consumables','machinesEquipment','transfers','returns','damagedTransfers','receiveTransfers','sales','paymentTracking','procurement','logistics','marketing','hrCustomerCare','reports','financial','userManagement','settings'].map(mod => (
 <th key={mod} style={{padding:'10px 4px',textAlign:'center',fontSize:9,textTransform:'uppercase',letterSpacing:0.3,borderBottom:'2px solid #dee2e6',minWidth:65,whiteSpace:'nowrap'}}>
 {mod === 'rawMaterials' ? 'Raw Mat.' : mod === 'stockManagement' ? 'Stock Mgt' : mod === 'productionTasks' ? 'Prod.Tasks' : mod === 'productionCompletions' ? 'Prod.Comp' : mod === 'machinesEquipment' ? 'Machines' : mod === 'transfers' ? 'Transfers' : mod === 'returns' ? 'Returns' : mod === 'damagedTransfers' ? 'Dmg Trans' : mod === 'receiveTransfers' ? 'Receive' : mod === 'paymentTracking' ? 'Pay & Debt' : mod === 'hrCustomerCare' ? 'HR/CustCare' : mod === 'userManagement' ? 'User Mgt' : mod.charAt(0).toUpperCase() + mod.slice(1)}
 </th>
 ))}
 <th style={{padding:'10px 8px',borderBottom:'2px solid #dee2e6'}}>Save</th>
 </tr>
 </thead>
 <tbody>
 {moduleAccessData.filter(u => u.is_active).map((u, idx) => {
 const isAdmin = u.role === 'admin';
 const mods = ['staff','attendance','products','rawMaterials','stockManagement','production','productionTasks','productionCompletions','consumables','machinesEquipment','transfers','returns','damagedTransfers','receiveTransfers','sales','paymentTracking','procurement','logistics','marketing','hrCustomerCare','reports','financial','userManagement','settings'];
 const allChecked = mods.every(m => u.modules[m]);
 return (
 <tr key={u.user_id} style={{background: idx%2===0?'#fff':'#fafbfc', opacity: isAdmin ? 0.6 : 1}}>
 <td style={{padding:'8px',fontWeight:600,position:'sticky',left:0,background:idx%2===0?'#fff':'#fafbfc',zIndex:1,borderBottom:'1px solid #eee'}}>
 <div>{u.full_name}</div>
 <div style={{fontSize:10,color:'#888',fontWeight:400}}>{u.email} ({u.role})</div>
 </td>
 <td style={{padding:'4px',textAlign:'center',borderBottom:'1px solid #eee'}}>
 <input type="checkbox" checked={allChecked} disabled={isAdmin} onChange={(e) => {
 const val = e.target.checked;
 setModuleAccessData(prev => prev.map(x => x.user_id === u.user_id ? {...x, modules: {...x.modules, ...Object.fromEntries(mods.map(m => [m, val]))}} : x));
 }} title="Toggle all modules" style={{cursor:isAdmin?'not-allowed':'pointer'}} />
 </td>
 {mods.map(mod => (
 <td key={mod} style={{padding:'4px',textAlign:'center',borderBottom:'1px solid #eee'}}>
 <input type="checkbox" checked={!!u.modules[mod]} disabled={isAdmin} onChange={() => {
 setModuleAccessData(prev => prev.map(x => x.user_id === u.user_id ? {...x, modules: {...x.modules, [mod]: !x.modules[mod]}} : x));
 }} style={{cursor:isAdmin?'not-allowed':'pointer',accentColor: u.modules[mod]?'#27ae60':'#ccc'}} />
 </td>
 ))}
 <td style={{padding:'4px',textAlign:'center',borderBottom:'1px solid #eee'}}>
 <button disabled={isAdmin || maSaving[u.user_id]} onClick={() => saveModuleAccess(u.user_id, u.modules)} style={{padding:'4px 10px',borderRadius:6,border:'none',background:isAdmin?'#eee':'#27ae60',color:isAdmin?'#999':'#fff',fontSize:10,fontWeight:700,cursor:isAdmin?'not-allowed':'pointer'}}>
 {maSaving[u.user_id] ? '...' : 'Save'}
 </button>
 </td>
 </tr>
 );
 })}
 </tbody>
 </table>
 </div>
 )}
 </div>

 {/* Warehouse Access Control */}
 <div className="settings-card" style={{gridColumn: '1 / -1'}}>
 <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:16}}>
 <h3>Warehouse Access Control</h3>
 <button className="btn btn-primary" onClick={fetchWarehouseAccessAll} style={{fontSize:12}}>{waLoading ? 'Loading...' : 'Load / Refresh Warehouse Access'}</button>
 </div>
 <p style={{fontSize:13,color:'#666',marginBottom:16}}>Grant or revoke access to each warehouse for individual users. Admins always have full access. Changes save immediately per user.</p>
 {warehouseAccessData.users.length === 0 && !waLoading && (
 <div style={{textAlign:'center',padding:30,color:'#aaa'}}>Click "Load / Refresh Warehouse Access" above to view and manage warehouse access for all users.</div>
 )}
 {warehouseAccessData.users.length > 0 && (
 <div style={{overflowX:'auto'}}>
 <table style={{width:'100%',borderCollapse:'collapse',fontSize:13}}>
 <thead>
 <tr style={{background:'#f1f3f5'}}>
 <th style={{padding:'10px 8px',textAlign:'left',position:'sticky',left:0,background:'#f1f3f5',zIndex:2,minWidth:200,borderBottom:'2px solid #dee2e6'}}>User</th>
 <th style={{padding:'10px 4px',textAlign:'center',fontSize:11,borderBottom:'2px solid #dee2e6',minWidth:30}} title="Select All">All</th>
 {warehouseAccessData.warehouses.map(wh => (
 <th key={wh.id} style={{padding:'10px 8px',textAlign:'center',fontSize:11,borderBottom:'2px solid #dee2e6',minWidth:120,whiteSpace:'nowrap'}}>
 {wh.name}
 </th>
 ))}
 <th style={{padding:'10px 8px',borderBottom:'2px solid #dee2e6'}}>Save</th>
 </tr>
 </thead>
 <tbody>
 {warehouseAccessData.users.map((u, idx) => {
 const isAdmin = u.role === 'admin';
 const allChecked = warehouseAccessData.warehouses.every(wh => u.warehouse_access[wh.id]);
 return (
 <tr key={u.user_id} style={{background: idx%2===0?'#fff':'#fafbfc', opacity: isAdmin ? 0.6 : 1}}>
 <td style={{padding:'8px',fontWeight:600,position:'sticky',left:0,background:idx%2===0?'#fff':'#fafbfc',zIndex:1,borderBottom:'1px solid #eee'}}>
 <div>{u.full_name}</div>
 <div style={{fontSize:10,color:'#888',fontWeight:400}}>{u.email} ({u.role})</div>
 </td>
 <td style={{padding:'4px',textAlign:'center',borderBottom:'1px solid #eee'}}>
 <input type="checkbox" checked={allChecked} disabled={isAdmin} onChange={(e) => {
 const val = e.target.checked;
 setWarehouseAccessData(prev => ({...prev, users: prev.users.map(x => x.user_id === u.user_id ? {...x, warehouse_access: {...x.warehouse_access, ...Object.fromEntries(prev.warehouses.map(wh => [wh.id, val]))}} : x)}));
 }} title="Toggle all warehouses" style={{cursor:isAdmin?'not-allowed':'pointer'}} />
 </td>
 {warehouseAccessData.warehouses.map(wh => (
 <td key={wh.id} style={{padding:'4px',textAlign:'center',borderBottom:'1px solid #eee'}}>
 <input type="checkbox" checked={!!u.warehouse_access[wh.id]} disabled={isAdmin} onChange={() => {
 setWarehouseAccessData(prev => ({...prev, users: prev.users.map(x => x.user_id === u.user_id ? {...x, warehouse_access: {...x.warehouse_access, [wh.id]: !x.warehouse_access[wh.id]}} : x)}));
 }} style={{cursor:isAdmin?'not-allowed':'pointer',accentColor: u.warehouse_access[wh.id]?'#3498db':'#ccc'}} />
 </td>
 ))}
 <td style={{padding:'4px',textAlign:'center',borderBottom:'1px solid #eee'}}>
 <button disabled={isAdmin || waSaving[u.user_id]} onClick={() => saveWarehouseAccess(u.user_id, u.warehouse_access)} style={{padding:'4px 10px',borderRadius:6,border:'none',background:isAdmin?'#eee':'#3498db',color:isAdmin?'#999':'#fff',fontSize:11,fontWeight:700,cursor:isAdmin?'not-allowed':'pointer'}}>
 {waSaving[u.user_id] ? '...' : 'Save'}
 </button>
 </td>
 </tr>
 );
 })}
 </tbody>
 </table>
 </div>
 )}
 </div>

 {/* Company Information */}
 <div className="settings-card">
 <h3>Company Information</h3>
 <div className="form-group"><label>Company Name</label><input type="text" defaultValue="AstroBSM StockMaster" placeholder="Enter company name"/></div>
 <div className="form-group"><label>Business Address</label><textarea rows="2" defaultValue="" placeholder="Enter business address"/></div>
 <div className="form-group"><label>Contact Phone</label><input type="tel" defaultValue="" placeholder="+234 xxx xxx xxxx"/></div>
 <div className="form-group"><label>Contact Email</label><input type="email" defaultValue="" placeholder="info@company.com"/></div>
 </div>

 {/* Localization */}
 <div className="settings-card">
 <h3>Localization</h3>
 <div className="form-group"><label>Currency</label><select defaultValue="NGN"><option value="NGN">Nigerian Naira (₦)</option><option value="USD">US Dollar ($)</option><option value="EUR">Euro ()</option></select></div>
 <div className="form-group"><label>Timezone</label><select defaultValue="Africa/Lagos"><option value="Africa/Lagos">West Africa Time (WAT)</option><option value="UTC">UTC</option></select></div>
 <div className="form-group"><label>Date Format</label><select defaultValue="DD/MM/YYYY"><option value="DD/MM/YYYY">DD/MM/YYYY</option><option value="MM/DD/YYYY">MM/DD/YYYY</option><option value="YYYY-MM-DD">YYYY-MM-DD</option></select></div>
 <div className="form-group"><label>Language</label><select defaultValue="en"><option value="en">English</option></select></div>
 </div>

 {/* Inventory Settings */}
 <div className="settings-card">
 <h3>Inventory Settings</h3>
 <div className="form-group"><label>Stock Valuation Method</label><select defaultValue="FIFO"><option value="FIFO">FIFO (First In First Out)</option><option value="LIFO">LIFO (Last In First Out)</option><option value="AVG">Average Cost</option></select></div>
 <div className="form-group"><label>Auto-Generate SKU</label><input type="checkbox" defaultChecked/> <span style={{marginLeft:'8px'}}>Enable automatic SKU generation</span></div>
 <div className="form-group"><label>SKU Prefix</label><input type="text" defaultValue="PRD" placeholder="e.g., PRD, ITEM"/></div>
 <div className="form-group"><label>Low Stock Warning Level</label><input type="number" defaultValue="10" placeholder="Minimum quantity"/></div>
 </div>

 {/* Sales Settings */}
 <div className="settings-card">
 <h3>Sales Settings</h3>
 <div className="form-group"><label>Default Order Type</label><select defaultValue="retail"><option value="retail">Retail</option><option value="wholesale">Wholesale</option></select></div>
 <div className="form-group"><label>Invoice Prefix</label><input type="text" defaultValue="INV" placeholder="e.g., INV, BILL"/></div>
 <div className="form-group"><label>Auto Invoice Numbering</label><input type="checkbox" defaultChecked/> <span style={{marginLeft:'8px'}}>Auto-generate invoice numbers</span></div>
 <div className="form-group"><label>Payment Terms (Days)</label><input type="number" defaultValue="30" placeholder="Default payment due days"/></div>
 </div>

 {/* Security Settings */}
 <div className="settings-card">
 <h3>Security Settings</h3>
 <div className="form-group"><label>Session Timeout (minutes)</label><input type="number" defaultValue="60" min="15" max="480"/></div>
 <div className="form-group"><label>Password Min Length</label><input type="number" defaultValue="8" min="6" max="32"/></div>
 <div className="form-group"><label>Enable 2-Factor Auth</label><input type="checkbox"/> <span style={{marginLeft:'8px'}}>Require 2FA for admin users</span></div>
 <div className="form-group"><label>Login Attempt Limit</label><input type="number" defaultValue="5" min="3" max="10"/></div>
 </div>

 </div>

 <div style={{marginTop:'24px', textAlign:'center'}}>
 <button className="btn btn-primary" style={{padding:'12px 48px', fontSize:'16px'}}>Save All Settings</button>
 </div>
 </div>
 )}

 {/* =============== COMMUNICATION MODULE =============== */}
 {activeModule === 'communication' && (() => {
 const userName = currentUser ? (currentUser.first_name || currentUser.email || 'User') : 'User';
 const isAdmin = !currentUser || currentUser.role === 'admin';
 const API = '/api/communication';

 // Load notices from backend on mount
 async function loadNotices() {
 try {
 const res = await authedFetch(`${API}/notices`);
 if (res.ok) { const data = await res.json(); setCommNotices(data); }
 } catch(e) { console.error('Failed to load notices', e); }
 }

 // Load chat messages for current channel
 async function loadMessages(ch) {
 try {
 const res = await authedFetch(`${API}/messages?channel=${ch || commChatChannel}`);
 if (res.ok) { const data = await res.json(); setCommMessages(data); }
 } catch(e) { console.error('Failed to load messages', e); }
 }

 // Auto-load data when module opens (use a flag to avoid re-fetching every render)
 if (!window._commNoticesLoaded) {
 window._commNoticesLoaded = true;
 loadNotices();
 // Mark current view as seen when module first opens
 if (commView === 'notices') markNoticesSeen();
 if (commView === 'chat') markMessagesSeen();
 }
 if (!window._commMsgChannel || window._commMsgChannel !== commChatChannel) {
 window._commMsgChannel = commChatChannel;
 loadMessages(commChatChannel);
 }

 // Periodic refresh every 15 seconds for cross-device sync
 if (!window._commRefreshInterval) {
 window._commRefreshInterval = setInterval(() => {
 if (document.visibilityState === 'visible') {
 loadNotices();
 loadMessages(commChatChannel);
 }
 }, 15000);
 }

 // Notice Board functions
 async function handlePostNotice() {
 if (!commNoticeForm.title.trim() || !commNoticeForm.content.trim()) {
 notify('Please fill in both title and content', 'error');
 return;
 }
 try {
 if (commEditingNotice) {
 const res = await authedFetch(`${API}/notices/${commEditingNotice.id}`, {
 method: 'PUT', headers: {'Content-Type':'application/json'},
 body: JSON.stringify({ title: commNoticeForm.title, content: commNoticeForm.content, priority: commNoticeForm.priority, category: commNoticeForm.category }),
 });
 if (!res.ok) throw new Error('Update failed');
 setCommEditingNotice(null);
 notify('Notice updated', 'success');
 } else {
 const res = await authedFetch(`${API}/notices?author=${encodeURIComponent(userName)}`, {
 method: 'POST', headers: {'Content-Type':'application/json'},
 body: JSON.stringify({ title: commNoticeForm.title, content: commNoticeForm.content, priority: commNoticeForm.priority, category: commNoticeForm.category }),
 });
 if (!res.ok) throw new Error('Post failed');
 notify('Notice posted', 'success');
 }
 setCommNoticeForm({ title: '', content: '', priority: 'normal', category: 'general' });
 setCommShowNoticeForm(false);
 await loadNotices();
 } catch(e) { notify('Failed to save notice: ' + e.message, 'error'); }
 }

 async function handleDeleteNotice(id) {
 if (!window.confirm('Delete this notice?')) return;
 try {
 await authedFetch(`${API}/notices/${id}`, { method: 'DELETE' });
 notify('Notice deleted', 'success');
 await loadNotices();
 } catch(e) { notify('Failed to delete notice', 'error'); }
 }

 async function handlePinNotice(id) {
 try {
 await authedFetch(`${API}/notices/${id}/pin`, { method: 'PATCH' });
 await loadNotices();
 } catch(e) { notify('Failed to pin notice', 'error'); }
 }

 // Chat functions
 async function handleSendMessage() {
 if (!commChatInput.trim()) return;
 try {
 const res = await authedFetch(`${API}/messages?sender=${encodeURIComponent(userName)}`, {
 method: 'POST', headers: {'Content-Type':'application/json'},
 body: JSON.stringify({ channel: commChatChannel, text: commChatInput.trim() }),
 });
 if (!res.ok) throw new Error('Send failed');
 setCommChatInput('');
 await loadMessages(commChatChannel);
 setTimeout(() => {
 if (commChatEndRef.current) commChatEndRef.current.scrollIntoView({ behavior: 'smooth' });
 }, 100);
 } catch(e) { notify('Failed to send message', 'error'); }
 }

 function handleChatKeyDown(e) {
 if (e.key === 'Enter' && !e.shiftKey) {
 e.preventDefault();
 handleSendMessage();
 }
 }

 // Video Conference functions
 function handleStartMeeting() {
 const room = commMeetingRoom.trim() || ('astrobsm-' + Date.now());
 const displayName = commMeetingName.trim() || userName;
 setCommMeetingRoom(room);
 setCommMeetingActive(true);
 // Load Jitsi Meet API
 setTimeout(() => {
 const container = commJitsiRef.current;
 if (!container) return;
 container.innerHTML = '';
 function loadJitsiAPI() {
 if (window.JitsiMeetExternalAPI) {
 initJitsi(container, room, displayName);
 return;
 }
 const script = document.createElement('script');
 script.src = 'https://8x8.vc/vpaas-magic-cookie-ef5ce88c523d41a599c8b1dc5b3ab765/external_api.js';
 script.onload = () => initJitsi(container, room, displayName);
 script.onerror = () => {
 // Fallback to meet.jit.si
 const s2 = document.createElement('script');
 s2.src = 'https://meet.jit.si/external_api.js';
 s2.onload = () => initJitsi(container, room, displayName);
 s2.onerror = () => { container.innerHTML = '<p style="color:red;text-align:center;padding:40px;">Failed to load video conference engine. Please check your internet connection.</p>'; };
 document.head.appendChild(s2);
 };
 document.head.appendChild(script);
 }
 function initJitsi(container, room, displayName) {
 try {
 const api = new window.JitsiMeetExternalAPI('meet.jit.si', {
 roomName: 'AstroBSM_' + room.replace(/[^a-zA-Z0-9_-]/g, '_'),
 parentNode: container,
 width: '100%',
 height: 600,
 configOverwrite: {
 startWithAudioMuted: true,
 startWithVideoMuted: false,
 disableDeepLinking: true,
 prejoinPageEnabled: false,
 },
 interfaceConfigOverwrite: {
 TOOLBAR_BUTTONS: ['microphone','camera','closedcaptions','desktop','fullscreen','fodeviceselection','hangup','chat','recording','livestreaming','etherpad','sharedvideo','shareaudio','settings','raisehand','videoquality','filmstrip','invite','feedback','stats','shortcuts','tileview','select-background','download','help'],
 SHOW_JITSI_WATERMARK: false,
 SHOW_WATERMARK_FOR_GUESTS: false,
 DEFAULT_BACKGROUND: '#0f2460',
 },
 userInfo: { displayName: displayName, email: currentUser?.email || '' },
 });
 api.addEventListener('readyToClose', () => {
 setCommMeetingActive(false);
 container.innerHTML = '';
 });
 } catch(e) {
 container.innerHTML = '<p style="color:red;text-align:center;padding:40px;">Could not initialize video conference: ' + e.message + '</p>';
 }
 }
 loadJitsiAPI();
 }, 200);
 }

 function handleEndMeeting() {
 setCommMeetingActive(false);
 if (commJitsiRef.current) commJitsiRef.current.innerHTML = '';
 }

 const channels = ['general', 'sales', 'production', 'hr', 'management'];
 const filteredMessages = commMessages;
 const priorityColors = { urgent: '#ef4444', high: '#f59e0b', normal: '#3b82f6', low: '#6b7280' };
 const categoryLabels = { general: 'General', hr: 'HR', policy: 'Policy', event: 'Event', safety: 'Safety', announcement: 'Announcement' };

 const sortedNotices = commNotices;

 return (
 <div className="module-content" style={{padding:0, background:'#f4f5f7', minHeight:'100vh'}}>
 {/* Header */}
 <div style={{background:'linear-gradient(135deg,#0f2460,#1a3a8a,#3b7ddd)', color:'#fff', padding:'20px 30px'}}>
 <h1 style={{fontSize:28, fontWeight:800, letterSpacing:2, margin:0, textAlign:'center'}}>COMMUNICATION HUB</h1>
 <p style={{fontSize:13, opacity:0.8, marginTop:4, letterSpacing:1, textAlign:'center'}}>Notice Board, Team Chat & Video Conference</p>
 </div>

 {/* Tab Navigation */}
 <div style={{display:'flex', gap:0, background:'#fff', borderBottom:'2px solid #e5e7eb', padding:'0 20px'}}>
 {[
 { key: 'notices', label: 'Notice Board', icon: 'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2' },
 { key: 'chat', label: 'Team Chat', icon: 'M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z' },
 { key: 'videoConference', label: 'Video Conference', icon: 'M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z' },
 ].map(tab => (
 <button key={tab.key} onClick={() => {
 setCommView(tab.key);
 if (tab.key === 'notices') markNoticesSeen();
 if (tab.key === 'chat') markMessagesSeen();
 }} style={{
 padding:'14px 24px', border:'none', background:'none', cursor:'pointer', fontSize:14, fontWeight:600,
 color: commView === tab.key ? '#1a3a8a' : '#6b7280',
 borderBottom: commView === tab.key ? '3px solid #1a3a8a' : '3px solid transparent',
 display:'flex', alignItems:'center', gap:8, transition:'all 0.2s', position:'relative',
 }}>
 <svg width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" viewBox="0 0 24 24"><path d={tab.icon}/></svg>
 {tab.label}
 {tab.key === 'notices' && commUnread.notices > 0 && (
 <span style={{background:'#ef4444',color:'#fff',fontSize:'11px',fontWeight:700,minWidth:'20px',height:'20px',borderRadius:'10px',display:'inline-flex',alignItems:'center',justifyContent:'center',padding:'0 6px',marginLeft:'4px',animation:'badgePulse 2s infinite'}}>{commUnread.notices}</span>
 )}
 {tab.key === 'chat' && commUnread.messages_total > 0 && (
 <span style={{background:'#22c55e',color:'#fff',fontSize:'11px',fontWeight:700,minWidth:'20px',height:'20px',borderRadius:'10px',display:'inline-flex',alignItems:'center',justifyContent:'center',padding:'0 6px',marginLeft:'4px',animation:'badgePulse 2s infinite'}}>{commUnread.messages_total}</span>
 )}
 </button>
 ))}
 </div>

 <div style={{maxWidth:1200, margin:'0 auto', padding:20}}>

 {/* ============ NOTICE BOARD ============ */}
 {commView === 'notices' && (
 <div>
 <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:20}}>
 <h2 style={{fontSize:20, fontWeight:700, color:'#1e293b', margin:0}}>General Notice Board</h2>
 {isAdmin && (
 <button onClick={() => { setCommShowNoticeForm(true); setCommEditingNotice(null); setCommNoticeForm({ title:'', content:'', priority:'normal', category:'general' }); }}
 style={{padding:'10px 20px', background:'#1a3a8a', color:'#fff', border:'none', borderRadius:6, fontWeight:600, cursor:'pointer', fontSize:13}}>
 + Post New Notice
 </button>
 )}
 </div>

 {/* New/Edit Notice Form */}
 {commShowNoticeForm && (
 <div style={{background:'#fff', borderRadius:8, padding:24, marginBottom:20, boxShadow:'0 2px 8px rgba(0,0,0,0.08)', border:'1px solid #e5e7eb'}}>
 <h3 style={{fontSize:16, fontWeight:700, marginBottom:16, color:'#1a3a8a'}}>{commEditingNotice ? 'Edit Notice' : 'Post New Notice'}</h3>
 <div style={{marginBottom:12}}>
 <label style={{display:'block', fontSize:12, fontWeight:600, marginBottom:4, color:'#374151'}}>Title *</label>
 <input type="text" value={commNoticeForm.title} onChange={e => setCommNoticeForm(f => ({...f, title: e.target.value}))}
 placeholder="Notice title..." style={{width:'100%', padding:'10px 14px', border:'1.5px solid #d1d5db', borderRadius:6, fontSize:14}} />
 </div>
 <div style={{display:'flex', gap:12, marginBottom:12}}>
 <div style={{flex:1}}>
 <label style={{display:'block', fontSize:12, fontWeight:600, marginBottom:4, color:'#374151'}}>Priority</label>
 <select value={commNoticeForm.priority} onChange={e => setCommNoticeForm(f => ({...f, priority: e.target.value}))}
 style={{width:'100%', padding:'10px 14px', border:'1.5px solid #d1d5db', borderRadius:6, fontSize:14}}>
 <option value="low">Low</option>
 <option value="normal">Normal</option>
 <option value="high">High</option>
 <option value="urgent">Urgent</option>
 </select>
 </div>
 <div style={{flex:1}}>
 <label style={{display:'block', fontSize:12, fontWeight:600, marginBottom:4, color:'#374151'}}>Category</label>
 <select value={commNoticeForm.category} onChange={e => setCommNoticeForm(f => ({...f, category: e.target.value}))}
 style={{width:'100%', padding:'10px 14px', border:'1.5px solid #d1d5db', borderRadius:6, fontSize:14}}>
 <option value="general">General</option>
 <option value="announcement">Announcement</option>
 <option value="hr">HR</option>
 <option value="policy">Policy</option>
 <option value="event">Event</option>
 <option value="safety">Safety</option>
 </select>
 </div>
 </div>
 <div style={{marginBottom:16}}>
 <label style={{display:'block', fontSize:12, fontWeight:600, marginBottom:4, color:'#374151'}}>Content *</label>
 <textarea value={commNoticeForm.content} onChange={e => setCommNoticeForm(f => ({...f, content: e.target.value}))}
 rows={6} placeholder="Type your notice..." style={{width:'100%', padding:'10px 14px', border:'1.5px solid #d1d5db', borderRadius:6, fontSize:14, lineHeight:1.6, resize:'vertical'}} />
 </div>
 <div style={{display:'flex', gap:10}}>
 <button onClick={handlePostNotice} style={{padding:'10px 24px', background:'#059669', color:'#fff', border:'none', borderRadius:6, fontWeight:600, cursor:'pointer', fontSize:13}}>
 {commEditingNotice ? 'Update Notice' : 'Post Notice'}
 </button>
 <button onClick={() => { setCommShowNoticeForm(false); setCommEditingNotice(null); }}
 style={{padding:'10px 24px', background:'#6b7280', color:'#fff', border:'none', borderRadius:6, fontWeight:600, cursor:'pointer', fontSize:13}}>Cancel</button>
 </div>
 </div>
 )}

 {/* Notices List */}
 {sortedNotices.length === 0 ? (
 <div style={{background:'#fff', borderRadius:8, padding:60, textAlign:'center', color:'#9ca3af', boxShadow:'0 1px 3px rgba(0,0,0,0.05)'}}>
 <svg width="48" height="48" fill="none" stroke="currentColor" strokeWidth="1.5" viewBox="0 0 24 24" style={{margin:'0 auto 16px', opacity:0.5}}><path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/></svg>
 <p style={{fontSize:16, fontWeight:600}}>No notices yet</p>
 <p style={{fontSize:13}}>When notices are posted, they will appear here.</p>
 </div>
 ) : (
 <div style={{display:'flex', flexDirection:'column', gap:12}}>
 {sortedNotices.map(notice => (
 <div key={notice.id} style={{
 background:'#fff', borderRadius:8, padding:20, boxShadow:'0 1px 3px rgba(0,0,0,0.05)',
 borderLeft: `4px solid ${priorityColors[notice.priority] || '#3b82f6'}`,
 position:'relative',
 }}>
 {notice.pinned && <div style={{position:'absolute', top:10, right:12, fontSize:11, fontWeight:700, color:'#f59e0b', textTransform:'uppercase', letterSpacing:1}}>Pinned</div>}
 <div style={{display:'flex', alignItems:'flex-start', gap:12}}>
 <div style={{flex:1}}>
 <div style={{display:'flex', alignItems:'center', gap:8, marginBottom:6}}>
 <h3 style={{fontSize:16, fontWeight:700, color:'#1e293b', margin:0}}>{notice.title}</h3>
 <span style={{fontSize:10, fontWeight:600, padding:'2px 8px', borderRadius:10, background: priorityColors[notice.priority] + '20', color: priorityColors[notice.priority], textTransform:'uppercase'}}>
 {notice.priority}
 </span>
 <span style={{fontSize:10, fontWeight:600, padding:'2px 8px', borderRadius:10, background:'#f1f5f9', color:'#475569'}}>
 {categoryLabels[notice.category] || notice.category}
 </span>
 </div>
 <p style={{fontSize:14, color:'#374151', lineHeight:1.7, margin:'0 0 10px 0', whiteSpace:'pre-wrap'}}>{notice.content}</p>
 <div style={{display:'flex', alignItems:'center', gap:16, fontSize:12, color:'#9ca3af'}}>
 <span>By: <strong style={{color:'#6b7280'}}>{notice.author}</strong></span>
 <span>{new Date(notice.created_at).toLocaleString()}</span>
 {notice.updated_at && <span style={{fontStyle:'italic'}}>(edited)</span>}
 </div>
 </div>
 {isAdmin && (
 <div style={{display:'flex', gap:6, flexShrink:0}}>
 <button onClick={() => handlePinNotice(notice.id)} title={notice.pinned ? 'Unpin' : 'Pin'}
 style={{width:32, height:32, border:'1px solid #e5e7eb', borderRadius:6, background: notice.pinned ? '#fef3c7' : '#fff', cursor:'pointer', display:'flex', alignItems:'center', justifyContent:'center'}}>
 <svg width="14" height="14" fill={notice.pinned ? '#f59e0b' : 'none'} stroke={notice.pinned ? '#f59e0b' : '#9ca3af'} strokeWidth="2" viewBox="0 0 24 24"><path d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z"/></svg>
 </button>
 <button onClick={() => { setCommEditingNotice(notice); setCommNoticeForm({ title: notice.title, content: notice.content, priority: notice.priority, category: notice.category }); setCommShowNoticeForm(true); }} title="Edit"
 style={{width:32, height:32, border:'1px solid #e5e7eb', borderRadius:6, background:'#fff', cursor:'pointer', display:'flex', alignItems:'center', justifyContent:'center'}}>
 <svg width="14" height="14" fill="none" stroke="#3b82f6" strokeWidth="2" viewBox="0 0 24 24"><path d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
 </button>
 <button onClick={() => handleDeleteNotice(notice.id)} title="Delete"
 style={{width:32, height:32, border:'1px solid #e5e7eb', borderRadius:6, background:'#fff', cursor:'pointer', display:'flex', alignItems:'center', justifyContent:'center'}}>
 <svg width="14" height="14" fill="none" stroke="#ef4444" strokeWidth="2" viewBox="0 0 24 24"><path d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
 </button>
 </div>
 )}
 </div>
 </div>
 ))}
 </div>
 )}
 </div>
 )}

 {/* ============ TEAM CHAT ============ */}
 {commView === 'chat' && (
 <div style={{display:'flex', gap:16, height:'calc(100vh - 260px)', minHeight:500}}>
 {/* Channel Sidebar */}
 <div style={{width:200, background:'#fff', borderRadius:8, boxShadow:'0 1px 3px rgba(0,0,0,0.05)', padding:16, flexShrink:0}}>
 <h3 style={{fontSize:13, fontWeight:700, color:'#6b7280', textTransform:'uppercase', letterSpacing:1, marginBottom:12}}>Channels</h3>
 {channels.map(ch => (
 <button key={ch} onClick={() => { setCommChatChannel(ch); window._commMsgChannel = ch; loadMessages(ch); }} style={{
 display:'block', width:'100%', textAlign:'left', padding:'10px 14px', border:'none', borderRadius:6, marginBottom:4, cursor:'pointer',
 background: commChatChannel === ch ? '#eef2ff' : 'transparent',
 color: commChatChannel === ch ? '#1a3a8a' : '#4b5563',
 fontWeight: commChatChannel === ch ? 700 : 500, fontSize:14, transition:'all 0.15s',
 }}>
 # {ch}
 <span style={{display:'block', fontSize:11, color:'#9ca3af', fontWeight:400}}>
 {commChatChannel === ch ? commMessages.length : ''} {ch}
 </span>
 </button>
 ))}
 </div>

 {/* Chat Area */}
 <div style={{flex:1, display:'flex', flexDirection:'column', background:'#fff', borderRadius:8, boxShadow:'0 1px 3px rgba(0,0,0,0.05)', overflow:'hidden'}}>
 {/* Chat Header */}
 <div style={{padding:'14px 20px', borderBottom:'1px solid #e5e7eb', background:'#fafbfc'}}>
 <h3 style={{fontSize:16, fontWeight:700, color:'#1e293b', margin:0}}># {commChatChannel}</h3>
 <p style={{fontSize:12, color:'#9ca3af', margin:'2px 0 0'}}>Team conversation channel</p>
 </div>

 {/* Messages Area */}
 <div style={{flex:1, overflowY:'auto', padding:20}}>
 {filteredMessages.length === 0 ? (
 <div style={{textAlign:'center', padding:60, color:'#9ca3af'}}>
 <svg width="48" height="48" fill="none" stroke="currentColor" strokeWidth="1.5" viewBox="0 0 24 24" style={{margin:'0 auto 16px', opacity:0.5}}><path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"/></svg>
 <p style={{fontSize:14, fontWeight:600}}>No messages in #{commChatChannel}</p>
 <p style={{fontSize:13}}>Start the conversation by typing a message below.</p>
 </div>
 ) : (
 filteredMessages.map((msg, i) => {
 const isOwn = msg.sender === userName;
 const showAvatar = i === 0 || filteredMessages[i-1].sender !== msg.sender;
 return (
 <div key={msg.id} style={{marginBottom: showAvatar ? 16 : 4, paddingLeft: showAvatar ? 0 : 42}}>
 {showAvatar && (
 <div style={{display:'flex', alignItems:'center', gap:8, marginBottom:4}}>
 <div style={{width:32, height:32, borderRadius:'50%', background: isOwn ? '#1a3a8a' : '#e5e7eb', color: isOwn ? '#fff' : '#6b7280', display:'flex', alignItems:'center', justifyContent:'center', fontSize:13, fontWeight:700, flexShrink:0}}>
 {msg.sender.charAt(0).toUpperCase()}
 </div>
 <span style={{fontWeight:700, fontSize:14, color:'#1e293b'}}>{msg.sender}</span>
 <span style={{fontSize:11, color:'#9ca3af'}}>{new Date(msg.created_at || msg.timestamp).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'})}</span>
 </div>
 )}
 <div style={{paddingLeft: showAvatar ? 42 : 0}}>
 <p style={{margin:0, fontSize:14, color:'#374151', lineHeight:1.6, whiteSpace:'pre-wrap'}}>{msg.text}</p>
 </div>
 </div>
 );
 })
 )}
 <div ref={commChatEndRef} />
 </div>

 {/* Message Input */}
 <div style={{padding:'12px 20px', borderTop:'1px solid #e5e7eb', background:'#fafbfc'}}>
 <div style={{display:'flex', gap:10}}>
 <input type="text" value={commChatInput} onChange={e => setCommChatInput(e.target.value)} onKeyDown={handleChatKeyDown}
 placeholder={`Message #${commChatChannel}...`}
 style={{flex:1, padding:'12px 16px', border:'1.5px solid #d1d5db', borderRadius:8, fontSize:14, background:'#fff'}} />
 <button onClick={handleSendMessage} disabled={!commChatInput.trim()}
 style={{padding:'12px 20px', background: commChatInput.trim() ? '#1a3a8a' : '#cbd5e1', color:'#fff', border:'none', borderRadius:8, fontWeight:600, cursor: commChatInput.trim() ? 'pointer' : 'default', fontSize:13, transition:'background 0.2s'}}>
 Send
 </button>
 </div>
 <p style={{fontSize:11, color:'#9ca3af', marginTop:6}}>Press Enter to send. Messages are visible to all team members in this channel.</p>
 </div>
 </div>
 </div>
 )}

 {/* ============ VIDEO CONFERENCE ============ */}
 {commView === 'videoConference' && (
 <div>
 {!commMeetingActive ? (
 <div style={{maxWidth:600, margin:'0 auto'}}>
 <div style={{background:'#fff', borderRadius:12, padding:32, boxShadow:'0 2px 12px rgba(0,0,0,0.08)', textAlign:'center'}}>
 <div style={{width:80, height:80, borderRadius:'50%', background:'linear-gradient(135deg,#0f2460,#3b7ddd)', margin:'0 auto 20px', display:'flex', alignItems:'center', justifyContent:'center'}}>
 <svg width="36" height="36" fill="none" stroke="white" strokeWidth="2" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"/></svg>
 </div>
 <h2 style={{fontSize:22, fontWeight:800, color:'#1e293b', marginBottom:6}}>Start Video Conference</h2>
 <p style={{fontSize:14, color:'#6b7280', marginBottom:24}}>Host or join a meeting with your team using Jitsi Meet</p>

 <div style={{textAlign:'left', maxWidth:400, margin:'0 auto'}}>
 <div style={{marginBottom:16}}>
 <label style={{display:'block', fontSize:12, fontWeight:600, marginBottom:4, color:'#374151'}}>Your Display Name</label>
 <input type="text" value={commMeetingName} onChange={e => setCommMeetingName(e.target.value)}
 placeholder={userName} style={{width:'100%', padding:'12px 16px', border:'1.5px solid #d1d5db', borderRadius:8, fontSize:14}} />
 </div>
 <div style={{marginBottom:24}}>
 <label style={{display:'block', fontSize:12, fontWeight:600, marginBottom:4, color:'#374151'}}>Meeting Room Name</label>
 <input type="text" value={commMeetingRoom} onChange={e => setCommMeetingRoom(e.target.value)}
 placeholder="e.g., weekly-standup, team-review" style={{width:'100%', padding:'12px 16px', border:'1.5px solid #d1d5db', borderRadius:8, fontSize:14}} />
 <p style={{fontSize:11, color:'#9ca3af', marginTop:4}}>Leave blank to auto-generate. Share the room name with others to join the same meeting.</p>
 </div>
 </div>

 <button onClick={handleStartMeeting} style={{
 padding:'14px 48px', background:'linear-gradient(135deg,#059669,#10b981)', color:'#fff', border:'none', borderRadius:8,
 fontSize:16, fontWeight:700, cursor:'pointer', letterSpacing:0.5, boxShadow:'0 4px 12px rgba(5,150,105,0.3)', transition:'transform 0.2s',
 }}>
 Start Meeting
 </button>

 <div style={{marginTop:24, padding:16, background:'#f8fafc', borderRadius:8, textAlign:'left'}}>
 <h4 style={{fontSize:13, fontWeight:700, color:'#374151', marginBottom:8}}>Features Available:</h4>
 <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:6, fontSize:12, color:'#6b7280'}}>
 <div style={{display:'flex', alignItems:'center', gap:6}}>
 <svg width="14" height="14" fill="#059669" viewBox="0 0 20 20"><path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd"/></svg>
 HD Video & Audio
 </div>
 <div style={{display:'flex', alignItems:'center', gap:6}}>
 <svg width="14" height="14" fill="#059669" viewBox="0 0 20 20"><path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd"/></svg>
 Screen Sharing
 </div>
 <div style={{display:'flex', alignItems:'center', gap:6}}>
 <svg width="14" height="14" fill="#059669" viewBox="0 0 20 20"><path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd"/></svg>
 In-meeting Chat
 </div>
 <div style={{display:'flex', alignItems:'center', gap:6}}>
 <svg width="14" height="14" fill="#059669" viewBox="0 0 20 20"><path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd"/></svg>
 Hand Raise
 </div>
 <div style={{display:'flex', alignItems:'center', gap:6}}>
 <svg width="14" height="14" fill="#059669" viewBox="0 0 20 20"><path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd"/></svg>
 Recording
 </div>
 <div style={{display:'flex', alignItems:'center', gap:6}}>
 <svg width="14" height="14" fill="#059669" viewBox="0 0 20 20"><path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd"/></svg>
 Tile View
 </div>
 <div style={{display:'flex', alignItems:'center', gap:6}}>
 <svg width="14" height="14" fill="#059669" viewBox="0 0 20 20"><path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd"/></svg>
 Background Blur
 </div>
 <div style={{display:'flex', alignItems:'center', gap:6}}>
 <svg width="14" height="14" fill="#059669" viewBox="0 0 20 20"><path fillRule="evenodd" d="M16.707 5.293a1 1 0 010 1.414l-8 8a1 1 0 01-1.414 0l-4-4a1 1 0 011.414-1.414L8 12.586l7.293-7.293a1 1 0 011.414 0z" clipRule="evenodd"/></svg>
 No Sign-up Required
 </div>
 </div>
 </div>
 </div>

 {/* Join a Meeting */}
 <div style={{background:'#fff', borderRadius:12, padding:24, boxShadow:'0 2px 8px rgba(0,0,0,0.05)', marginTop:20}}>
 <h3 style={{fontSize:15, fontWeight:700, color:'#1e293b', marginBottom:12}}>Share Meeting Link</h3>
 <p style={{fontSize:13, color:'#6b7280', marginBottom:12}}>Copy and share this link with team members to invite them:</p>
 <div style={{display:'flex', gap:8}}>
 <input type="text" readOnly value={commMeetingRoom ? `https://meet.jit.si/AstroBSM_${commMeetingRoom.replace(/[^a-zA-Z0-9_-]/g, '_')}` : 'Enter a room name above first'}
 style={{flex:1, padding:'10px 14px', border:'1.5px solid #d1d5db', borderRadius:6, fontSize:13, background:'#f9fafb', color:'#374151'}} />
 <button onClick={() => {
 const link = commMeetingRoom ? `https://meet.jit.si/AstroBSM_${commMeetingRoom.replace(/[^a-zA-Z0-9_-]/g, '_')}` : '';
 if (link) { navigator.clipboard.writeText(link); notify('Meeting link copied!', 'success'); }
 else notify('Enter a room name first', 'error');
 }} style={{padding:'10px 16px', background:'#1a3a8a', color:'#fff', border:'none', borderRadius:6, fontWeight:600, cursor:'pointer', fontSize:12, whiteSpace:'nowrap'}}>
 Copy Link
 </button>
 </div>
 </div>
 </div>
 ) : (
 /* Active Meeting */
 <div>
 <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:12}}>
 <div>
 <h2 style={{fontSize:18, fontWeight:700, color:'#1e293b', margin:0}}>Meeting: {commMeetingRoom}</h2>
 <p style={{fontSize:12, color:'#6b7280', margin:'2px 0 0'}}>Powered by Jitsi Meet - Free & Open Source</p>
 </div>
 <button onClick={handleEndMeeting} style={{padding:'10px 20px', background:'#ef4444', color:'#fff', border:'none', borderRadius:6, fontWeight:600, cursor:'pointer', fontSize:13}}>
 End Meeting
 </button>
 </div>
 <div ref={commJitsiRef} style={{width:'100%', minHeight:600, borderRadius:8, overflow:'hidden', background:'#000', boxShadow:'0 4px 20px rgba(0,0,0,0.2)'}} />
 </div>
 )}
 </div>
 )}

 </div>
 </div>
 );
 })()}

 {/* Production Tasks Module (embedded standalone tracker) */}
 {activeModule === 'productionTasks' && (
 <div style={{padding:0,margin:0}}>
 <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',padding:'8px 12px',background:'#f4f6fb',borderBottom:'1px solid #e5e7eb'}}>
 <div>
 <h2 style={{margin:0,fontSize:18,color:'#0a3d62'}}>Production Tasks Tracker</h2>
 <small style={{color:'#6b7280'}}>Assign duties, log completion qty, and confirm production work</small>
 </div>
 <a href="/production-tasks.html" target="_blank" rel="noopener noreferrer" className="btn btn-secondary" style={{fontSize:12}}>Open in new tab</a>
 </div>
 <iframe
 title="Production Tasks"
 src="/production-tasks.html"
 style={{width:'100%',height:'calc(100vh - 110px)',border:0,display:'block',background:'#f4f6fb'}}
 />
 </div>
 )}

 {/* Training Academy Module (slide-deck style training) */}
 {activeModule === 'training' && (
 <div style={{padding:0,margin:0}}>
 <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',padding:'8px 12px',background:'#f4f6fb',borderBottom:'1px solid #e5e7eb'}}>
 <div>
 <h2 style={{margin:0,fontSize:18,color:'#0a3d62'}}>Training Academy</h2>
 <small style={{color:'#6b7280'}}>Role-based slide presentations — Sales, Marketing, Production, Customer Care, Procurement, Management</small>
 </div>
 <a href="/training.html" target="_blank" rel="noopener noreferrer" className="btn btn-secondary" style={{fontSize:12}}>Open in new tab</a>
 </div>
 <iframe
 title="Training Academy"
 src="/training.html"
 style={{width:'100%',height:'calc(100vh - 110px)',border:0,display:'block',background:'#0a1d2e'}}
 />
 </div>
 )}

 {/* Personalized clock-in greeting toast */}
 {clockInGreeting && (
 <div style={{position:'fixed',top:80,right:20,zIndex:9999,maxWidth:420,background: clockInGreeting.tier==='early'?'#dcfce7':clockInGreeting.tier==='on_time'?'#dbeafe':clockInGreeting.tier==='mild_late'?'#fef3c7':'#fee2e2',color:'#111',border:'2px solid '+(clockInGreeting.tier==='early'?'#16a34a':clockInGreeting.tier==='on_time'?'#2563eb':clockInGreeting.tier==='mild_late'?'#d97706':'#dc2626'),borderRadius:12,padding:'14px 16px',boxShadow:'0 8px 24px rgba(0,0,0,0.18)'}}>
 <div style={{display:'flex',justifyContent:'space-between',alignItems:'flex-start',gap:8}}>
 <strong style={{fontSize:14,textTransform:'uppercase',letterSpacing:0.4}}>
 {clockInGreeting.tier==='early' ? '🌟 Early Arrival' : clockInGreeting.tier==='on_time' ? '✅ On Time' : clockInGreeting.tier==='mild_late' ? '⚠️ Mild Lateness' : '⛔ Severe Lateness'}
 </strong>
 <button onClick={()=>setClockInGreeting(null)} style={{border:0,background:'transparent',fontSize:18,cursor:'pointer',lineHeight:1}}>×</button>
 </div>
 <div style={{marginTop:6,fontWeight:600}}>{clockInGreeting.staff_name}</div>
 <div style={{marginTop:4,fontSize:13,lineHeight:1.45,whiteSpace:'pre-wrap'}}>{clockInGreeting.message}</div>
 </div>
 )}

 {/* Company Radio mini-player (collapses to a tiny floating icon) */}
 {radioEnabled && !radioPanelOpen && (
 <button
 onClick={()=>setRadioPanelOpen(true)}
 title={radioCurrent ? `Now: ${radioCurrent.title}` : (radioIsLeader ? 'Company Radio (broadcasting)' : 'Company Radio')}
 style={{position:'fixed',bottom:16,right:16,zIndex:9997,width:44,height:44,borderRadius:'50%',border:0,cursor:'pointer',background:'linear-gradient(135deg,#0a3d62 0%,#1e5a86 100%)',color:'#fff',boxShadow:'0 4px 14px rgba(0,0,0,0.3)',display:'flex',alignItems:'center',justifyContent:'center',fontSize:18,position:'fixed'}}
 >
 <span style={{animation: radioCurrent ? 'pulse 1.2s infinite' : 'none'}}>{radioMuted ? '🔇' : (radioCurrent ? '🔴' : '📡')}</span>
 {(radioQueue.length > 0 || radioCurrent) && (
 <span style={{position:'absolute',top:-2,right:-2,minWidth:16,height:16,padding:'0 4px',borderRadius:8,background:'#fbbf24',color:'#0a3d62',fontSize:10,fontWeight:800,display:'flex',alignItems:'center',justifyContent:'center',boxShadow:'0 1px 3px rgba(0,0,0,0.3)'}}>{radioQueue.length + (radioCurrent ? 1 : 0)}</span>
 )}
 </button>
 )}
 {radioEnabled && radioPanelOpen && (
 <div style={{position:'fixed',bottom:16,right:16,zIndex:9997,width:300,background:'linear-gradient(135deg,#0a3d62 0%,#1e5a86 100%)',color:'#fff',borderRadius:12,boxShadow:'0 8px 24px rgba(0,0,0,0.35)',overflow:'hidden',fontSize:12}}>
 <div style={{display:'flex',alignItems:'center',gap:8,padding:'7px 10px',background:'rgba(0,0,0,0.18)'}}>
 <span style={{fontSize:14,animation: radioCurrent ? 'pulse 1.2s infinite' : 'none'}}>{radioCurrent ? '🔴' : (radioIsLeader ? '📡' : '🔇')}</span>
 <strong style={{flex:1,letterSpacing:0.4,fontSize:11}}>COMPANY RADIO</strong>
 <button onClick={()=>setRadioPanelOpen(false)} title="Minimize" style={{background:'transparent',border:0,color:'#fff',cursor:'pointer',fontSize:14,lineHeight:1,padding:'0 4px'}}>−</button>
 </div>
 <div style={{padding:'7px 10px',display:'flex',alignItems:'center',gap:6}}>
 <button onClick={()=>{ setRadioMuted(m=>!m); if (!radioMuted) { try { window.speechSynthesis?.cancel(); radioAudioRef.current?.pause?.(); } catch {} setRadioCurrent(null); }}} style={{background:'rgba(255,255,255,0.15)',border:0,color:'#fff',borderRadius:6,padding:'3px 7px',cursor:'pointer',fontSize:12}} title={radioMuted?'Unmute':'Mute'}>{radioMuted?'🔇':'🔊'}</button>
 <input type="range" min="0" max="1" step="0.05" value={radioVolume} onChange={e=>setRadioVolume(parseFloat(e.target.value))} style={{flex:1,accentColor:'#fbbf24'}} title="Volume"/>
 <button onClick={()=>{ try { window.speechSynthesis?.cancel(); radioAudioRef.current?.pause?.(); } catch {} setRadioCurrent(null); setRadioQueue([]);}} style={{background:'rgba(255,255,255,0.15)',border:0,color:'#fff',borderRadius:6,padding:'3px 7px',cursor:'pointer',fontSize:12}} title="Skip / clear">⏭</button>
 </div>
 {radioCurrent && (
 <div style={{padding:'4px 10px 6px',borderTop:'1px solid rgba(255,255,255,0.12)'}}>
 <div style={{fontWeight:700,fontSize:10,color:'#fbbf24',textTransform:'uppercase'}}>NOW · {radioCurrent.title}</div>
 <div style={{fontSize:11,opacity:0.92,marginTop:2,lineHeight:1.3}}>{(radioCurrent.message||'').slice(0,80)}{(radioCurrent.message||'').length>80?'…':''}</div>
 </div>
 )}
 {radioQueue.length > 0 && (
 <div style={{padding:'2px 10px 4px',fontSize:10,opacity:0.85}}>Queue: {radioQueue.length} pending</div>
 )}
 <div style={{maxHeight:180,overflowY:'auto',padding:'4px 10px 8px',borderTop:'1px solid rgba(255,255,255,0.12)',background:'rgba(0,0,0,0.18)'}}>
 <div style={{fontWeight:700,fontSize:10,marginBottom:3,opacity:0.9}}>📜 Recent broadcasts</div>
 {radioHistory.length === 0 && (<div style={{opacity:0.65,fontSize:11}}>Nothing yet.</div>)}
 {radioHistory.slice(0, 15).map((ev, i) => (
 <div key={ev.id+'-'+i} style={{padding:'4px 0',borderBottom:'1px solid rgba(255,255,255,0.08)'}}>
 <div style={{fontSize:9,color:'#fbbf24',fontWeight:700,textTransform:'uppercase',letterSpacing:0.4}}>
 {ev.type.replace(/_/g,' ')} · {new Date(ev.created_at).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'})}
 </div>
 <div style={{fontSize:11,opacity:0.92,lineHeight:1.3}}>{ev.message || ev.title}</div>
 </div>
 ))}
 <div style={{display:'flex',justifyContent:'space-between',marginTop:6,gap:6}}>
 <button onClick={()=>setRadioHistory([])} style={{flex:1,background:'rgba(255,255,255,0.12)',border:0,color:'#fff',padding:'4px',borderRadius:6,cursor:'pointer',fontSize:10}}>Clear log</button>
 <button onClick={()=>{ setRadioEnabled(false); setRadioPanelOpen(false); }} style={{flex:1,background:'rgba(220,38,38,0.55)',border:0,color:'#fff',padding:'4px',borderRadius:6,cursor:'pointer',fontSize:10}}>Turn off</button>
 </div>
 </div>
 </div>
 )}
 {!radioEnabled && (
 <button onClick={()=>setRadioEnabled(true)} title="Turn on Company Radio" style={{position:'fixed',bottom:16,right:16,zIndex:9997,width:44,height:44,borderRadius:'50%',background:'#475569',color:'#fff',border:0,boxShadow:'0 4px 12px rgba(0,0,0,0.25)',cursor:'pointer',fontSize:18,opacity:0.6}}>📡</button>
 )}

 {/* Announcements Module (admin only) */}
 {activeModule === 'announcements' && (() => {
 const isAdmin = !currentUser || currentUser.role === 'admin';
 if (!isAdmin) return <div style={{padding:24}}>Admin access required.</div>;

 const reload = () => authedFetch('/api/announcements/').then(r=>r.ok?r.json():[]).then(d=>setAnnouncements(Array.isArray(d)?d:[]));

 async function saveAnnouncement(e) {
 e?.preventDefault?.();
 const f = announcementForm;
 if (!f.title.trim() || !f.message.trim() || !f.scheduled_time) {
 notify('Title, message and time are required', 'error'); return;
 }
 try {
 const url = editingAnnouncement ? `/api/announcements/${editingAnnouncement.id}` : '/api/announcements/';
 const method = editingAnnouncement ? 'PUT' : 'POST';
 const body = { ...f, scheduled_date: f.scheduled_date || null };
 const r = await authedFetch(url, {method, headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
 if (!r.ok) throw new Error(await r.text());
 const result = await r.json().catch(()=>({}));
 const annId = editingAnnouncement ? editingAnnouncement.id : result.id;
 // If audio attached, upload it now
 if (announcementAudioFile && annId) {
 const fd = new FormData();
 fd.append('file', announcementAudioFile, announcementAudioFile.name || 'audio.webm');
 const ar = await authedFetch(`/api/announcements/${annId}/audio`, { method:'POST', body: fd });
 if (!ar.ok) throw new Error('Audio upload failed');
 }
 notify(editingAnnouncement ? 'Announcement updated' : 'Announcement created', 'success');
 setEditingAnnouncement(null);
 setAnnouncementAudioFile(null);
 setAnnouncementForm({ title:'', message:'', scheduled_date:'', scheduled_time:'09:00', repeat_type:'daily', repeat_days:'0,1,2,3,4', is_active:true });
 reload();
 } catch(err) { notify('Save failed: '+err.message, 'error'); }
 }
 async function startAnnouncementRecording() {
 if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
 notify('Microphone not supported in this browser', 'error'); return;
 }
 try {
 const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
 const mr = new MediaRecorder(stream);
 announcementChunksRef.current = [];
 mr.ondataavailable = (ev) => { if (ev.data && ev.data.size > 0) announcementChunksRef.current.push(ev.data); };
 mr.onstop = () => {
 const blob = new Blob(announcementChunksRef.current, { type: 'audio/webm' });
 const file = new File([blob], `recording-${Date.now()}.webm`, { type: 'audio/webm' });
 setAnnouncementAudioFile(file);
 stream.getTracks().forEach(t => t.stop());
 };
 mr.start();
 announcementRecorderRef.current = mr;
 setAnnouncementRecording(true);
 } catch(e) { notify('Mic access denied: '+e.message, 'error'); }
 }
 function stopAnnouncementRecording() {
 const mr = announcementRecorderRef.current;
 if (mr && mr.state !== 'inactive') mr.stop();
 setAnnouncementRecording(false);
 }
 async function deleteAnnouncement(id) {
 if (!window.confirm('Delete this announcement?')) return;
 const r = await authedFetch(`/api/announcements/${id}`, {method:'DELETE'});
 if (r.ok) { notify('Deleted', 'success'); reload(); }
 }
 async function uploadAudio(id, file) {
 if (!file) return;
 const fd = new FormData(); fd.append('file', file);
 const r = await authedFetch(`/api/announcements/${id}/audio`, {method:'POST', body: fd});
 if (r.ok) { notify('Audio uploaded', 'success'); reload(); }
 else notify('Upload failed', 'error');
 }
 async function savePolicy(e) {
 e?.preventDefault?.();
 const r = await authedFetch('/api/announcements/policy', {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(announcementPolicy)});
 if (r.ok) { notify('Policy saved', 'success'); const d = await r.json(); setAnnouncementPolicy(d); }
 else notify('Save failed', 'error');
 }
 async function previewGreeting(staffId, mockTime) {
 if (!staffId) { notify('Pick a staff to preview', 'error'); return; }
 const r = await authedFetch('/api/announcements/clock-in-greeting', {
 method:'POST', headers:{'Content-Type':'application/json'},
 body: JSON.stringify({ staff_id: staffId, clock_in_time: mockTime || new Date().toISOString() })
 });
 if (r.ok) setAnnouncementPreview(await r.json());
 }
 const dayNames = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
 const repeatDaysSet = new Set((announcementForm.repeat_days||'').split(',').map(s=>s.trim()).filter(s=>s!==''));
 function toggleDay(idx) {
 const k = String(idx);
 if (repeatDaysSet.has(k)) repeatDaysSet.delete(k); else repeatDaysSet.add(k);
 setAnnouncementForm(p=>({...p, repeat_days: Array.from(repeatDaysSet).sort().join(',')}));
 }

 return (
 <div style={{padding:'12px 16px'}}>
 <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:12}}>
 <div>
 <h2 style={{margin:0,fontSize:20,color:'#0a3d62'}}>Announcements</h2>
 <small style={{color:'#6b7280'}}>Schedule company-wide announcements with audio · personalize clock-in messages by lateness policy</small>
 </div>
 </div>

 {/* Attendance Policy editor */}
 {announcementPolicy && (
 <form onSubmit={savePolicy} style={{background:'#f8fafc',border:'1px solid #e2e8f0',borderRadius:10,padding:14,marginBottom:16}}>
 <strong style={{display:'block',marginBottom:8,color:'#0a3d62'}}>📋 Attendance Policy (drives personalized clock-in messages)</strong>
 <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(160px,1fr))',gap:10}}>
 <label style={{fontSize:12}}>Company Name<input value={announcementPolicy.company_name||''} onChange={e=>setAnnouncementPolicy(p=>({...p,company_name:e.target.value}))} style={{width:'100%',padding:6,border:'1px solid #cbd5e1',borderRadius:6}}/></label>
 <label style={{fontSize:12}}>Expected Start Hour<input type="number" min="0" max="23" value={announcementPolicy.expected_start_hour||0} onChange={e=>setAnnouncementPolicy(p=>({...p,expected_start_hour:parseInt(e.target.value)||0}))} style={{width:'100%',padding:6,border:'1px solid #cbd5e1',borderRadius:6}}/></label>
 <label style={{fontSize:12}}>Expected Start Minute<input type="number" min="0" max="59" value={announcementPolicy.expected_start_minute||0} onChange={e=>setAnnouncementPolicy(p=>({...p,expected_start_minute:parseInt(e.target.value)||0}))} style={{width:'100%',padding:6,border:'1px solid #cbd5e1',borderRadius:6}}/></label>
 <label style={{fontSize:12}}>Early Threshold (min before)<input type="number" value={announcementPolicy.early_threshold_minutes||0} onChange={e=>setAnnouncementPolicy(p=>({...p,early_threshold_minutes:parseInt(e.target.value)||0}))} style={{width:'100%',padding:6,border:'1px solid #cbd5e1',borderRadius:6}}/></label>
 <label style={{fontSize:12}}>On-Time Grace (min after)<input type="number" value={announcementPolicy.on_time_grace_minutes||0} onChange={e=>setAnnouncementPolicy(p=>({...p,on_time_grace_minutes:parseInt(e.target.value)||0}))} style={{width:'100%',padding:6,border:'1px solid #cbd5e1',borderRadius:6}}/></label>
 <label style={{fontSize:12}}>Mild Late Max (min)<input type="number" value={announcementPolicy.mild_late_max_minutes||0} onChange={e=>setAnnouncementPolicy(p=>({...p,mild_late_max_minutes:parseInt(e.target.value)||0}))} style={{width:'100%',padding:6,border:'1px solid #cbd5e1',borderRadius:6}}/></label>
 </div>
 <div style={{marginTop:10,display:'flex',gap:8,alignItems:'center'}}>
 <button type="submit" className="btn btn-primary">Save Policy</button>
 <small style={{color:'#64748b'}}>Tiers: Early ≥ threshold before · On-time within grace · Mild late ≤ max · Severe late after that</small>
 </div>
 </form>
 )}

 {/* Preview personalized greeting */}
 <div style={{background:'#fff',border:'1px solid #e2e8f0',borderRadius:10,padding:14,marginBottom:16}}>
 <strong style={{display:'block',marginBottom:8,color:'#0a3d62'}}>🔍 Preview Personalized Clock-In Greeting</strong>
 <div style={{display:'flex',gap:8,flexWrap:'wrap',alignItems:'center'}}>
 <select id="announcePreviewStaff" style={{padding:6,border:'1px solid #cbd5e1',borderRadius:6,minWidth:200}}>
 <option value="">Select staff…</option>
 {(data.staff||[]).map(s=>(<option key={s.id} value={s.id}>{s.first_name} {s.last_name}</option>))}
 </select>
 <input type="datetime-local" id="announcePreviewTime" style={{padding:6,border:'1px solid #cbd5e1',borderRadius:6}}/>
 <button className="btn btn-secondary" onClick={()=>{
 const sid = document.getElementById('announcePreviewStaff').value;
 const t = document.getElementById('announcePreviewTime').value;
 const iso = t ? new Date(t).toISOString() : null;
 previewGreeting(sid, iso);
 }}>Preview</button>
 </div>
 {announcementPreview && (
 <div style={{marginTop:10,padding:10,borderRadius:8,background: announcementPreview.tier==='early'?'#dcfce7':announcementPreview.tier==='on_time'?'#dbeafe':announcementPreview.tier==='mild_late'?'#fef3c7':'#fee2e2'}}>
 <strong>{announcementPreview.tier?.toUpperCase()}</strong> · {announcementPreview.staff_name} · {announcementPreview.minutes_diff} min diff<br/>
 <span style={{whiteSpace:'pre-wrap'}}>{announcementPreview.message}</span>
 </div>
 )}
 </div>

 {/* Create / Edit form */}
 <form onSubmit={saveAnnouncement} style={{background:'#fff',border:'1px solid #e2e8f0',borderRadius:10,padding:14,marginBottom:16}}>
 <strong style={{display:'block',marginBottom:8,color:'#0a3d62'}}>{editingAnnouncement ? '✏️ Edit Announcement' : '➕ New Announcement'}</strong>
 <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(200px,1fr))',gap:10}}>
 <label style={{fontSize:12}}>Title<input value={announcementForm.title} onChange={e=>setAnnouncementForm(p=>({...p,title:e.target.value}))} style={{width:'100%',padding:6,border:'1px solid #cbd5e1',borderRadius:6}}/></label>
 <label style={{fontSize:12}}>Time (HH:MM)<input type="time" value={announcementForm.scheduled_time} onChange={e=>setAnnouncementForm(p=>({...p,scheduled_time:e.target.value}))} style={{width:'100%',padding:6,border:'1px solid #cbd5e1',borderRadius:6}}/></label>
 <label style={{fontSize:12}}>Date (optional, one-shot)<input type="date" value={announcementForm.scheduled_date||''} onChange={e=>setAnnouncementForm(p=>({...p,scheduled_date:e.target.value}))} style={{width:'100%',padding:6,border:'1px solid #cbd5e1',borderRadius:6}}/></label>
 <label style={{fontSize:12}}>Repeat<select value={announcementForm.repeat_type} onChange={e=>setAnnouncementForm(p=>({...p,repeat_type:e.target.value}))} style={{width:'100%',padding:6,border:'1px solid #cbd5e1',borderRadius:6}}>
 <option value="none">None (one-shot)</option>
 <option value="daily">Daily</option>
 <option value="weekly">Weekly (pick days)</option>
 <option value="monthly">Monthly (same day-of-month)</option>
 </select></label>
 <label style={{fontSize:12,gridColumn:'1 / -1'}}>Message<textarea rows={3} value={announcementForm.message} onChange={e=>setAnnouncementForm(p=>({...p,message:e.target.value}))} style={{width:'100%',padding:6,border:'1px solid #cbd5e1',borderRadius:6}}/></label>
 </div>
 {announcementForm.repeat_type === 'weekly' && (
 <div style={{marginTop:8}}>
 <small style={{color:'#64748b'}}>Pick weekdays:</small>{' '}
 {dayNames.map((d,i)=>(
 <button type="button" key={d} onClick={()=>toggleDay(i)} style={{margin:'2px 4px',padding:'4px 10px',borderRadius:6,border:'1px solid #cbd5e1',background: repeatDaysSet.has(String(i))?'#0a3d62':'#fff',color: repeatDaysSet.has(String(i))?'#fff':'#0a3d62',cursor:'pointer'}}>{d}</button>
 ))}
 </div>
 )}
 <label style={{display:'inline-flex',gap:6,marginTop:10,fontSize:13}}>
 <input type="checkbox" checked={!!announcementForm.is_active} onChange={e=>setAnnouncementForm(p=>({...p,is_active:e.target.checked}))}/> Active
 </label>
 {/* Audio attachment (upload or record) */}
 <div style={{marginTop:12,padding:10,border:'1px dashed #cbd5e1',borderRadius:8,background:'#f8fafc'}}>
 <strong style={{fontSize:13,color:'#0a3d62'}}>🎤 Audio Message (optional)</strong>
 <div style={{marginTop:6,display:'flex',gap:8,flexWrap:'wrap',alignItems:'center'}}>
 <label className="btn btn-secondary" style={{fontSize:12,cursor:'pointer'}}>
 📁 Choose Audio File
 <input type="file" accept="audio/*" style={{display:'none'}} onChange={e=>setAnnouncementAudioFile(e.target.files?.[0]||null)}/>
 </label>
 {!announcementRecording ? (
 <button type="button" className="btn btn-secondary" style={{fontSize:12}} onClick={startAnnouncementRecording}>🔴 Record from Mic</button>
 ) : (
 <button type="button" className="btn btn-danger" style={{fontSize:12}} onClick={stopAnnouncementRecording}>⏹️ Stop Recording</button>
 )}
 {announcementAudioFile && (
 <>
 <span style={{fontSize:12,color:'#16a34a'}}>✓ {announcementAudioFile.name} ({Math.round(announcementAudioFile.size/1024)} KB)</span>
 <audio controls src={announcementAudioUrl} style={{height:28,maxWidth:200}}/>
 <button type="button" className="btn btn-secondary" style={{fontSize:11,padding:'2px 8px'}} onClick={()=>setAnnouncementAudioFile(null)}>Clear</button>
 </>
 )}
 </div>
 <small style={{color:'#64748b',display:'block',marginTop:6}}>Allowed: mp3, wav, ogg, m4a, webm, aac · max 15 MB. Plays automatically at the scheduled time.</small>
 </div>
 <div style={{marginTop:10,display:'flex',gap:8}}>
 <button type="submit" className="btn btn-primary">{editingAnnouncement ? 'Update' : 'Create'}</button>
 {editingAnnouncement && (<button type="button" className="btn btn-secondary" onClick={()=>{setEditingAnnouncement(null);setAnnouncementAudioFile(null);setAnnouncementForm({title:'',message:'',scheduled_date:'',scheduled_time:'09:00',repeat_type:'daily',repeat_days:'0,1,2,3,4',is_active:true});}}>Cancel</button>)}
 </div>
 </form>

 {/* List */}
 <div style={{background:'#fff',border:'1px solid #e2e8f0',borderRadius:10,padding:14}}>
 <strong style={{display:'block',marginBottom:8,color:'#0a3d62'}}>📅 Scheduled Announcements ({announcements.length})</strong>
 {announcements.length === 0 ? (
 <div style={{color:'#94a3b8',padding:14,textAlign:'center'}}>No announcements scheduled yet.</div>
 ) : (
 <div style={{overflowX:'auto'}}>
 <table style={{width:'100%',borderCollapse:'collapse',fontSize:13}}>
 <thead><tr style={{background:'#f1f5f9'}}>
 <th style={{textAlign:'left',padding:8}}>Title</th>
 <th style={{textAlign:'left',padding:8}}>Time</th>
 <th style={{textAlign:'left',padding:8}}>Repeat</th>
 <th style={{textAlign:'left',padding:8}}>Audio</th>
 <th style={{textAlign:'left',padding:8}}>Active</th>
 <th style={{textAlign:'left',padding:8}}>Actions</th>
 </tr></thead>
 <tbody>
 {announcements.map(a => (
 <tr key={a.id} style={{borderTop:'1px solid #e2e8f0'}}>
 <td style={{padding:8}}><strong>{a.title}</strong><div style={{color:'#64748b',fontSize:11,maxWidth:300,whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>{a.message}</div></td>
 <td style={{padding:8}}>{(a.scheduled_time||'').toString().slice(0,5)}{a.scheduled_date?` · ${a.scheduled_date}`:''}</td>
 <td style={{padding:8}}>{a.repeat_type}{a.repeat_days?` (${a.repeat_days})`:''}</td>
 <td style={{padding:8}}>
 {a.audio_filename ? (
 <audio controls src={`/api/announcements/audio/${a.audio_filename}`} style={{height:28,maxWidth:160}} />
 ) : <span style={{color:'#94a3b8'}}>—</span>}
 <div><label style={{cursor:'pointer',color:'#2563eb',fontSize:11}}>Upload<input type="file" accept="audio/*" style={{display:'none'}} onChange={e=>uploadAudio(a.id, e.target.files?.[0])}/></label></div>
 </td>
 <td style={{padding:8}}>{a.is_active ? '✅' : '⏸️'}</td>
 <td style={{padding:8}}>
 <button className="btn btn-secondary" style={{fontSize:11,padding:'2px 8px',marginRight:4}} onClick={()=>{setEditingAnnouncement(a);setAnnouncementForm({title:a.title||'',message:a.message||'',scheduled_date:a.scheduled_date||'',scheduled_time:(a.scheduled_time||'09:00').toString().slice(0,5),repeat_type:a.repeat_type||'daily',repeat_days:a.repeat_days||'',is_active:!!a.is_active});}}>Edit</button>
 <button className="btn btn-danger" style={{fontSize:11,padding:'2px 8px'}} onClick={()=>deleteAnnouncement(a.id)}>Delete</button>
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 </div>
 )}
 </div>
 </div>
 );
 })()}

 {/* Profits Module (admin-only realtime profit tracking) */}
 {activeModule === 'profits' && (
 <div style={{padding:0,margin:0}}>
 <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',padding:'8px 12px',background:'#f4f6fb',borderBottom:'1px solid #e5e7eb'}}>
 <div>
 <h2 style={{margin:0,fontSize:18,color:'#0a3d62'}}>Profits</h2>
 <small style={{color:'#6b7280'}}>Real-time profit per payment · weekly approval & settlement (admin only)</small>
 </div>
 <a href="/profits.html" target="_blank" rel="noopener noreferrer" className="btn btn-secondary" style={{fontSize:12}}>Open in new tab</a>
 </div>
 <iframe
 title="Profits"
 src="/profits.html"
 style={{width:'100%',height:'calc(100vh - 110px)',border:0,display:'block',background:'#f4f6fb'}}
 />
 </div>
 )}

 {/* SOP / GMP Module */}
 {activeModule === 'sop' && (() => {
 const API = '/api/sop';
 const isAdmin = !currentUser || currentUser.role === 'admin';

 async function loadSopTemplates() {
 try {
 const res = await authedFetch(`${API}/templates`);
 if (res.ok) { const data = await res.json(); setSopTemplates(data); }
 } catch(e) { console.error('Failed to load SOP templates', e); }
 }
 async function loadSopRecords(code) {
 try {
 const url = code ? `${API}/records?sop_code=${encodeURIComponent(code)}` : `${API}/records`;
 const res = await authedFetch(url);
 if (res.ok) { const data = await res.json(); setSopRecords(data); }
 } catch(e) { console.error('Failed to load SOP records', e); }
 }
 if (!window._sopTemplatesLoaded) {
 window._sopTemplatesLoaded = true;
 loadSopTemplates();
 loadSopRecords();
 }

 const selected = sopTemplates.find(t => t.sop_code === sopSelectedCode) || null;

 function setField(name, value) {
 setSopFormData(prev => ({ ...prev, [name]: value }));
 }
 function setChecklistItem(name, checked) {
 setSopFormData(prev => ({ ...prev, checklist: { ...prev.checklist, [name]: checked } }));
 }
 function setNumericItem(name, value) {
 setSopFormData(prev => ({ ...prev, numeric_inputs: { ...prev.numeric_inputs, [name]: value } }));
 }
 function resetForm() {
 setSopFormData({
 operator_name: '', supervisor_name: '', executed_at: '', batch_number: '',
 material_equipment_used: '', checklist: {}, numeric_inputs: {},
 operator_signature: '', supervisor_signature: '', comments: '', deviation: ''
 });
 }

 async function submitRecord() {
 if (!selected) { notify('Select an SOP first', 'error'); return; }
 if (!sopFormData.operator_name || !sopFormData.supervisor_name || !sopFormData.executed_at || !sopFormData.batch_number) {
 notify('Operator, supervisor, date/time and batch number are required', 'error');
 return;
 }
 if (!sopFormData.operator_signature || !sopFormData.supervisor_signature) {
 notify('Both signatures are required', 'error');
 return;
 }
 setSopSubmitting(true);
 try {
 const payload = {
 sop_code: selected.sop_code,
 form_data: {
 operator_name: sopFormData.operator_name,
 supervisor_name: sopFormData.supervisor_name,
 executed_at: new Date(sopFormData.executed_at).toISOString(),
 batch_number: sopFormData.batch_number,
 material_equipment_used: sopFormData.material_equipment_used,
 checklist: sopFormData.checklist,
 numeric_inputs: sopFormData.numeric_inputs,
 operator_signature: sopFormData.operator_signature,
 supervisor_signature: sopFormData.supervisor_signature,
 comments: sopFormData.comments,
 deviation: sopFormData.deviation,
 },
 comments: sopFormData.comments || null,
 deviation: sopFormData.deviation || null,
 };
 const res = await authedFetch(`${API}/records`, {
 method: 'POST', headers: {'Content-Type':'application/json'},
 body: JSON.stringify(payload),
 });
 if (!res.ok) {
 const err = await res.json().catch(() => ({}));
 const msg = Array.isArray(err.detail) ? err.detail.join('; ') : (err.detail || 'Submission failed');
 throw new Error(msg);
 }
 notify('SOP record submitted successfully', 'success');
 resetForm();
 loadSopRecords(selected.sop_code);
 setSopViewTab('records');
 } catch(e) {
 notify(e.message || 'Submission failed', 'error');
 } finally {
 setSopSubmitting(false);
 }
 }

 async function approveRecord(rec) {
 const approver = prompt('Approver name (QA/Production Manager):');
 if (!approver) return;
 try {
 const res = await authedFetch(`${API}/records/${rec.id}/approve`, {
 method: 'PATCH', headers: {'Content-Type':'application/json'},
 body: JSON.stringify({ approved_by: approver })
 });
 if (!res.ok) throw new Error('Approval failed');
 notify('Record approved', 'success');
 loadSopRecords(selected ? selected.sop_code : null);
 } catch(e) { notify(e.message, 'error'); }
 }

 const formSchema = (selected && selected.form_schema) || {};
 const checklistItems = formSchema.checklist || [];
 const numericItems = formSchema.numeric_fields || [];
 const extraFields = (formSchema.fields || []).filter(f => !['operator_name','supervisor_name','executed_at','batch_number','operator_signature','supervisor_signature','material_equipment_used','comments','deviation'].includes(f.name));

 return (
 <div className="module-container">
 <h2 style={{margin:'0 0 12px 0'}}>SOP / GMP Compliance</h2>
 <p style={{color:'#64748b',marginTop:0}}>Standard Operating Procedures aligned with NAFDAC and WHO GMP. Select an SOP to view documentation, submit a digital execution record, and review history.</p>

 <div style={{display:'grid',gridTemplateColumns:'minmax(220px,260px) 1fr',gap:16,marginTop:12}}>
 {/* SOP list */}
 <div style={{border:'1px solid #e2e8f0',borderRadius:8,background:'#fff',maxHeight:'70vh',overflowY:'auto'}}>
 <div style={{padding:'10px 12px',borderBottom:'1px solid #e2e8f0',fontWeight:700,background:'#f8fafc'}}>SOP Library ({sopTemplates.length})</div>
 {sopTemplates.length === 0 && <div style={{padding:12,color:'#94a3b8'}}>Loading SOPs...</div>}
 {sopTemplates.map(t => (
 <div key={t.sop_code}
 onClick={() => { setSopSelectedCode(t.sop_code); setSopViewTab('document'); loadSopRecords(t.sop_code); }}
 style={{padding:'10px 12px',borderBottom:'1px solid #f1f5f9',cursor:'pointer',background: sopSelectedCode===t.sop_code ? '#eef2ff' : 'transparent'}}>
 <div style={{fontWeight:700,fontSize:13,color:'#1e293b'}}>{t.sop_number}</div>
 <div style={{fontSize:12,color:'#475569',marginTop:2}}>{t.title}</div>
 <div style={{fontSize:10,color:'#94a3b8',marginTop:4}}>v{t.version}  {t.effective_date}</div>
 </div>
 ))}
 </div>

 {/* Detail */}
 <div style={{border:'1px solid #e2e8f0',borderRadius:8,background:'#fff',padding:16,minHeight:400}}>
 {!selected && <div style={{color:'#94a3b8',textAlign:'center',padding:40}}>Select an SOP from the left to view details, complete a checklist, and submit records.</div>}
 {selected && (
 <>
 <div style={{display:'flex',justifyContent:'space-between',alignItems:'flex-start',marginBottom:12,gap:12,flexWrap:'wrap'}}>
 <div>
 <h3 style={{margin:'0 0 4px 0'}}>{selected.title}</h3>
 <div style={{fontSize:12,color:'#64748b'}}>SOP #: <strong>{selected.sop_number}</strong>  Version: <strong>{selected.version}</strong>  Effective: <strong>{selected.effective_date}</strong></div>
 </div>
 <div style={{display:'flex',gap:6,flexWrap:'wrap'}}>
 {['document','form','records'].map(tab => (
 <button key={tab} onClick={() => setSopViewTab(tab)}
 style={{padding:'6px 12px',borderRadius:6,border:'1px solid #cbd5e1',background: sopViewTab===tab ? '#4f46e5' : '#fff',color: sopViewTab===tab ? '#fff' : '#1e293b',cursor:'pointer',fontWeight:600,fontSize:12,textTransform:'uppercase'}}>
 {tab === 'document' ? 'Document' : tab === 'form' ? 'New Record' : `Records (${sopRecords.length})`}
 </button>
 ))}
 <a href={`${API}/templates/${encodeURIComponent(selected.sop_code)}/pdf`} target="_blank" rel="noopener noreferrer"
 style={{padding:'6px 12px',borderRadius:6,border:'1px solid #059669',background:'#059669',color:'#fff',textDecoration:'none',fontWeight:700,fontSize:12,textTransform:'uppercase'}}>
 Download PDF
 </a>
 </div>
 </div>

 {sopViewTab === 'document' && (
 <div style={{maxHeight:'60vh',overflowY:'auto',paddingRight:8}}>
 {Object.entries(selected.document || {}).map(([section, content]) => (
 <div key={section} style={{marginBottom:14}}>
 <h4 style={{margin:'0 0 6px 0',color:'#4f46e5',textTransform:'capitalize'}}>{section.replace(/_/g,' ')}</h4>
 {Array.isArray(content) ? (
 <ol style={{margin:'0 0 0 18px',padding:0}}>
 {content.map((item, idx) => (
 <li key={idx} style={{marginBottom:4,fontSize:13,color:'#334155'}}>
 {typeof item === 'object' ? (
 <div>{Object.entries(item).map(([k,v]) => <div key={k}><strong style={{textTransform:'capitalize'}}>{k.replace(/_/g,' ')}:</strong> {String(v)}</div>)}</div>
 ) : String(item)}
 </li>
 ))}
 </ol>
 ) : typeof content === 'object' && content !== null ? (
 <div style={{background:'#f8fafc',padding:8,borderRadius:6}}>
 {Object.entries(content).map(([k,v]) => <div key={k} style={{fontSize:13,marginBottom:3}}><strong style={{textTransform:'capitalize'}}>{k.replace(/_/g,' ')}:</strong> {Array.isArray(v) ? v.join(', ') : String(v)}</div>)}
 </div>
 ) : (
 <div style={{fontSize:13,color:'#334155',whiteSpace:'pre-wrap'}}>{String(content)}</div>
 )}
 </div>
 ))}
 {(selected.validation_rules || []).length > 0 && (
 <div style={{marginTop:14,padding:10,background:'#fef3c7',border:'1px solid #fbbf24',borderRadius:6}}>
 <h4 style={{margin:'0 0 6px 0',color:'#92400e'}}>Validation Rules</h4>
 <ul style={{margin:'0 0 0 18px',padding:0}}>
 {selected.validation_rules.map((r, i) => <li key={i} style={{fontSize:12,color:'#78350f'}}>{r}</li>)}
 </ul>
 </div>
 )}
 </div>
 )}

 {sopViewTab === 'form' && (
 <div style={{maxHeight:'60vh',overflowY:'auto',paddingRight:8}}>
 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:10,marginBottom:12}}>
 <label style={{fontSize:12}}>Operator Name *<input type="text" value={sopFormData.operator_name} onChange={e => setField('operator_name', e.target.value)} className="form-input" /></label>
 <label style={{fontSize:12}}>Supervisor Name *<input type="text" value={sopFormData.supervisor_name} onChange={e => setField('supervisor_name', e.target.value)} className="form-input" /></label>
 <label style={{fontSize:12}}>Date / Time Executed *<input type="datetime-local" value={sopFormData.executed_at} onChange={e => setField('executed_at', e.target.value)} className="form-input" /></label>
 <label style={{fontSize:12}}>Batch Number *<input type="text" value={sopFormData.batch_number} onChange={e => setField('batch_number', e.target.value)} className="form-input" /></label>
 </div>
 <label style={{fontSize:12,display:'block',marginBottom:10}}>Materials / Equipment Used
 <textarea value={sopFormData.material_equipment_used} onChange={e => setField('material_equipment_used', e.target.value)} className="form-input" rows={2} />
 </label>

 {checklistItems.length > 0 && (
 <div style={{marginBottom:12,padding:10,background:'#f8fafc',borderRadius:6}}>
 <div style={{fontWeight:700,fontSize:13,marginBottom:8}}>Checklist</div>
 {checklistItems.map(item => (
 <label key={item.name} style={{display:'flex',alignItems:'flex-start',gap:8,marginBottom:6,fontSize:13}}>
 <input type="checkbox" checked={!!sopFormData.checklist[item.name]} onChange={e => setChecklistItem(item.name, e.target.checked)} style={{marginTop:3}} />
 <span>{item.label || item.name}{item.required && <span style={{color:'#ef4444'}}> *</span>}</span>
 </label>
 ))}
 </div>
 )}

 {numericItems.length > 0 && (
 <div style={{marginBottom:12,padding:10,background:'#f0f9ff',borderRadius:6}}>
 <div style={{fontWeight:700,fontSize:13,marginBottom:8}}>Numeric Measurements</div>
 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:8}}>
 {numericItems.map(nf => (
 <label key={nf.name} style={{fontSize:12}}>
 {nf.label || nf.name}{nf.required && <span style={{color:'#ef4444'}}> *</span>}
 {nf.unit && <span style={{color:'#64748b'}}> ({nf.unit})</span>}
 <input type="number" step="any" min={nf.min} max={nf.max}
 value={sopFormData.numeric_inputs[nf.name] || ''}
 onChange={e => setNumericItem(nf.name, e.target.value)} className="form-input" />
 {(nf.min !== undefined || nf.max !== undefined) && <small style={{color:'#94a3b8'}}>Range: {nf.min ?? ''} to {nf.max ?? ''}</small>}
 </label>
 ))}
 </div>
 </div>
 )}

 {extraFields.length > 0 && (
 <div style={{marginBottom:12,padding:10,background:'#fdf4ff',borderRadius:6}}>
 <div style={{fontWeight:700,fontSize:13,marginBottom:8}}>Additional Fields</div>
 {extraFields.map(f => (
 <label key={f.name} style={{display:'block',fontSize:12,marginBottom:8}}>
 {f.label || f.name}{f.required && <span style={{color:'#ef4444'}}> *</span>}
 <input type={f.type === 'number' ? 'number' : 'text'}
 value={sopFormData.numeric_inputs[f.name] || ''}
 onChange={e => setNumericItem(f.name, e.target.value)} className="form-input" />
 </label>
 ))}
 </div>
 )}

 <label style={{fontSize:12,display:'block',marginBottom:8}}>Comments
 <textarea value={sopFormData.comments} onChange={e => setField('comments', e.target.value)} className="form-input" rows={2} />
 </label>
 <label style={{fontSize:12,display:'block',marginBottom:8}}>Deviation (if any  minimum 10 characters)
 <textarea value={sopFormData.deviation} onChange={e => setField('deviation', e.target.value)} className="form-input" rows={2} placeholder="Describe any deviation from the SOP..." />
 </label>

 <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:10,marginTop:8,padding:10,background:'#fef2f2',borderRadius:6,border:'1px dashed #fca5a5'}}>
 <label style={{fontSize:12}}>Operator Signature *<input type="text" value={sopFormData.operator_signature} onChange={e => setField('operator_signature', e.target.value)} className="form-input" placeholder="Type full name as signature" /></label>
 <label style={{fontSize:12}}>Supervisor Signature *<input type="text" value={sopFormData.supervisor_signature} onChange={e => setField('supervisor_signature', e.target.value)} className="form-input" placeholder="Type full name as signature" /></label>
 </div>

 <div style={{display:'flex',gap:8,marginTop:14}}>
 <button onClick={submitRecord} disabled={sopSubmitting}
 style={{padding:'10px 20px',background:'#16a34a',color:'#fff',border:'none',borderRadius:6,cursor:'pointer',fontWeight:700}}>
 {sopSubmitting ? 'Submitting...' : 'Submit SOP Record'}
 </button>
 <button onClick={resetForm} type="button"
 style={{padding:'10px 20px',background:'#fff',color:'#475569',border:'1px solid #cbd5e1',borderRadius:6,cursor:'pointer',fontWeight:600}}>Reset</button>
 </div>
 </div>
 )}

 {sopViewTab === 'records' && (
 <div style={{maxHeight:'60vh',overflowY:'auto'}}>
 {sopRecords.length === 0 && <div style={{color:'#94a3b8',padding:20,textAlign:'center'}}>No records yet for this SOP.</div>}
 <table style={{width:'100%',borderCollapse:'collapse',fontSize:12}}>
 <thead style={{background:'#f1f5f9'}}>
 <tr>
 <th style={{padding:8,textAlign:'left',borderBottom:'1px solid #e2e8f0'}}>Date</th>
 <th style={{padding:8,textAlign:'left',borderBottom:'1px solid #e2e8f0'}}>Batch</th>
 <th style={{padding:8,textAlign:'left',borderBottom:'1px solid #e2e8f0'}}>Operator</th>
 <th style={{padding:8,textAlign:'left',borderBottom:'1px solid #e2e8f0'}}>Supervisor</th>
 <th style={{padding:8,textAlign:'left',borderBottom:'1px solid #e2e8f0'}}>Status</th>
 <th style={{padding:8,textAlign:'left',borderBottom:'1px solid #e2e8f0'}}>Approved By</th>
 <th style={{padding:8,textAlign:'left',borderBottom:'1px solid #e2e8f0'}}>Actions</th>
 </tr>
 </thead>
 <tbody>
 {sopRecords.map(r => (
 <tr key={r.id} style={{borderBottom:'1px solid #f1f5f9'}}>
 <td style={{padding:8}}>{new Date(r.executed_at).toLocaleString()}</td>
 <td style={{padding:8}}><strong>{r.batch_number}</strong></td>
 <td style={{padding:8}}>{r.operator_name}</td>
 <td style={{padding:8}}>{r.supervisor_name}</td>
 <td style={{padding:8}}>
 <span style={{padding:'2px 8px',borderRadius:10,fontSize:11,fontWeight:700,
 background: r.status==='approved' ? '#dcfce7' : r.status==='deviation_raised' ? '#fee2e2' : '#fef3c7',
 color: r.status==='approved' ? '#166534' : r.status==='deviation_raised' ? '#991b1b' : '#92400e'}}>
 {r.status}
 </span>
 </td>
 <td style={{padding:8}}>{r.approved_by || ''}</td>
 <td style={{padding:8,whiteSpace:'nowrap'}}>
 <a href={`${API}/records/${r.id}/pdf`} target="_blank" rel="noopener noreferrer"
 style={{padding:'4px 10px',background:'#059669',color:'#fff',borderRadius:4,fontSize:11,fontWeight:600,textDecoration:'none',marginRight:6}}>PDF</a>
 {isAdmin && r.status !== 'approved' && (
 <button onClick={() => approveRecord(r)} style={{padding:'4px 10px',background:'#4f46e5',color:'#fff',border:'none',borderRadius:4,cursor:'pointer',fontSize:11,fontWeight:600}}>Approve</button>
 )}
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 </div>
 )}
 </>
 )}
 </div>
 </div>
 </div>
 );
 })()}

 {/* ASTRO Letters - Admin Only */}
 {activeModule === 'letters' && (() => {
 // Scoped refs for the Letters module
 const LOGO_SRC = '/letterhead-logo.png';
 const SIG_SRC = '/signature.png';

 function escHtml(s) {
 const d = document.createElement('div');
 d.textContent = s;
 return d.innerHTML;
 }

 function formatLetterDate(dateStr) {
 if (!dateStr) return '';
 const d = new Date(dateStr + 'T00:00:00');
 const months = ['January','February','March','April','May','June','July','August','September','October','November','December'];
 const day = d.getDate();
 let suffix = 'th';
 if (day === 1 || day === 21 || day === 31) suffix = 'st';
 else if (day === 2 || day === 22) suffix = 'nd';
 else if (day === 3 || day === 23) suffix = 'rd';
 return day + suffix + ' ' + months[d.getMonth()] + ' ' + d.getFullYear();
 }

 function buildLetterHTML(data, logoSrc, sigSrc) {
 let html = '';
 html += '<div style="background:linear-gradient(135deg,#0f2460 0%,#1a3a8a 50%,#2952b8 100%);padding:14px 48px;display:flex;align-items:center;gap:16px;">';
 html += '<div style="width:65px;height:65px;flex-shrink:0;background:#fff;border-radius:6px;padding:3px;display:flex;align-items:center;justify-content:center;"><img src="' + logoSrc + '" alt="ASTRO BSM Logo" style="width:100%;height:100%;object-fit:contain;border-radius:4px;"></div>';
 html += '<div style="flex:1;">';
 html += '<h1 style="font-family:Georgia,serif;font-size:22px;font-weight:900;letter-spacing:4px;margin:0;text-transform:uppercase;color:#fff;">BONNESANTE MEDICALS</h1>';
 html += '<div style="font-size:11px;font-weight:600;letter-spacing:1px;opacity:0.95;color:#fff;margin-top:2px;font-family:Georgia,serif;">INNOVATIVE HEALTH CARE SOLUTIONS</div>';
 html += '<div style="font-size:9px;font-weight:500;letter-spacing:4px;opacity:0.75;margin-top:2px;color:#fff;font-family:Georgia,serif;">. . . R E W A R D I N G&ensp;T R U S T&ensp;W I T H&ensp;E X C E L L E N C E</div>';
 html += '</div></div>';
 html += '<div style="background:rgba(26,58,138,0.06);padding:6px 48px;font-size:9px;color:#555;border-bottom:1px solid #e5e7eb;font-family:Georgia,serif;">';
 html += 'No 17A Isuofia / No 6B Peace Avenue, Federal Housing Estate, Trans-Ekulu, Enugu, Enugu State. &nbsp;|&nbsp; astrobsm@gmail.com';
 html += '</div></div>';
 html += '<div style="padding:19px 48px 19px 48px;flex:1;font-family:Georgia,serif;font-size:12pt;line-height:1.7;color:#111;">';
 if (data.date) html += '<div style="margin-bottom:16px;font-family:Georgia,serif;font-size:12pt;">' + escHtml(formatLetterDate(data.date)) + '</div>';
 if (data.recipTitle || data.recipOrg) {
 html += '<div style="margin-bottom:12px;font-family:Georgia,serif;font-size:12pt;line-height:1.5;">';
 if (data.recipTitle) html += '<p style="margin:0;font-weight:700;">' + escHtml(data.recipTitle) + '</p>';
 if (data.recipOrg) data.recipOrg.split('\n').forEach(line => { const l = line.trim(); if (l) html += '<p style="margin:0;">' + escHtml(l) + '</p>'; });
 html += '</div>';
 }
 html += '<div style="margin-bottom:12px;font-family:Georgia,serif;font-size:12pt;">' + escHtml(data.salutation) + '</div>';
 if (data.subject) html += '<div style="margin-bottom:12px;font-family:Georgia,serif;font-size:12pt;font-weight:700;text-decoration:underline;">RE: ' + escHtml(data.subject).toUpperCase() + '</div>';
 html += '<div style="font-family:Georgia,serif;font-size:12pt;line-height:1.7;text-align:justify;">';
 if (data.bodyOpen) html += '<p>' + escHtml(data.bodyOpen) + '</p>';
 if (data.bodyMid) data.bodyMid.split('\n\n').forEach(para => { const p = para.trim(); if (p) html += '<p>' + escHtml(p) + '</p>'; });
 if (data.bullets && data.bullets.length) {
 html += '<ul>';
 data.bullets.forEach(b => {
 let parts = b.split(' \u2013 ');
 if (parts.length < 2) parts = b.split(' \u2014 ');
 if (parts.length < 2) parts = b.split(' - ');
 if (parts.length >= 2) html += '<li><strong>' + escHtml(parts[0].trim()) + '</strong> \u2013 ' + escHtml(parts.slice(1).join(' \u2013 ').trim()) + '</li>';
 else html += '<li>' + escHtml(b) + '</li>';
 });
 html += '</ul>';
 }
 if (data.bodyClose) html += '<p>' + escHtml(data.bodyClose) + '</p>';
 html += '</div>';
 html += '<div style="margin-top:20px;font-family:Georgia,serif;font-size:12pt;">' + escHtml(data.closing) + '</div>';
 html += '<div style="margin-top:8px;"><img src="' + sigSrc + '" alt="Signature" style="width:160px;height:auto;display:block;"></div>';
 html += '<div style="font-family:Georgia,serif;font-size:12pt;line-height:1.5;">';
 if (data.sigName) html += '<div style="font-weight:700;">' + escHtml(data.sigName) + '</div>';
 if (data.sigTitle) html += '<div>' + escHtml(data.sigTitle) + '</div>';
 if (data.sigPhone) html += '<div>Phone: ' + escHtml(data.sigPhone) + '</div>';
 if (data.sigEmail) html += '<div>Email: ' + escHtml(data.sigEmail) + '</div>';
 html += '</div></div>';
 html += '<div style="margin-top:auto;background:linear-gradient(135deg,#0f2460,#1a3a8a);color:#fff;padding:10px 48px;font-size:9px;display:flex;justify-content:space-between;align-items:center;flex-shrink:0;font-family:Georgia,serif;">';
 html += '<div><span style="display:block;margin:2px 0;"><span style="display:inline-block;width:14px;text-align:center;margin-right:4px;">&#9742;</span>+234 909 253 4292, +234 909 253 4293</span>';
 html += '<span style="display:block;margin:2px 0;"><span style="display:inline-block;width:14px;text-align:center;margin-right:4px;">&#9993;</span>astrobsm@gmail.com</span></div>';
 html += '<div><span style="display:block;margin:2px 0;"><span style="display:inline-block;width:14px;text-align:center;margin-right:4px;">&#9679;</span>No 17A Isuofia / No 6B Peace Avenue</span>';
 html += '<span style="display:block;margin:2px 0;">&nbsp;&nbsp;&nbsp;Federal Housing Estate, Trans-Ekulu, Enugu, Enugu State</span></div>';
 html += '</div>';
 return html;
 }

 function gatherLetterData() {
 const salBase = document.getElementById('lt_salutation')?.value || 'Dear Sir';
 const salName = (document.getElementById('lt_sal_name')?.value || '').trim();
 const salutation = salName ? salBase.replace(/,$/, '') + ' ' + salName + ',' : salBase;
 const bullets = [];
 document.querySelectorAll('#lt-bullet-list input[type="text"]').forEach(inp => { const v = inp.value.trim(); if (v) bullets.push(v); });
 return {
 date: document.getElementById('lt_date')?.value || '',
 recipTitle: (document.getElementById('lt_recip_title')?.value || '').trim(),
 recipOrg: (document.getElementById('lt_recip_org')?.value || '').trim(),
 salutation,
 subject: (document.getElementById('lt_subject')?.value || '').trim(),
 bodyOpen: (document.getElementById('lt_body_open')?.value || '').trim(),
 bodyMid: (document.getElementById('lt_body_mid')?.value || '').trim(),
 bullets,
 bodyClose: (document.getElementById('lt_body_close')?.value || '').trim(),
 closing: document.getElementById('lt_closing')?.value || 'Yours sincerely,',
 sigName: (document.getElementById('lt_sig_name')?.value || '').trim(),
 sigTitle: (document.getElementById('lt_sig_title')?.value || '').trim(),
 sigPhone: (document.getElementById('lt_sig_phone')?.value || '').trim(),
 sigEmail: (document.getElementById('lt_sig_email')?.value || '').trim(),
 };
 }

 function handleLetterPreview() {
 const data = gatherLetterData();
 const page = document.getElementById('lt-letter-page');
 if (page) page.innerHTML = buildLetterHTML(data, LOGO_SRC, SIG_SRC);
 }

 function handleLetterClear() {
 ['lt_recip_title','lt_recip_org','lt_sal_name','lt_subject','lt_body_open','lt_body_mid','lt_body_close'].forEach(id => {
 const el = document.getElementById(id);
 if (el) el.value = '';
 });
 const salEl = document.getElementById('lt_salutation');
 if (salEl) salEl.selectedIndex = 0;
 const closeEl = document.getElementById('lt_closing');
 if (closeEl) closeEl.selectedIndex = 0;
 const bulletList = document.getElementById('lt-bullet-list');
 if (bulletList) bulletList.innerHTML = '';
 const page = document.getElementById('lt-letter-page');
 if (page) page.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;min-height:500px;color:#aaa;font-size:16px;text-align:center;padding:40px;">Fill in the form and click <strong style="margin:0 6px;">Preview</strong> to see your letter.</div>';
 }

 function handleAddBullet() {
 const list = document.getElementById('lt-bullet-list');
 if (!list) return;
 const count = list.children.length + 1;
 const wrap = document.createElement('div');
 wrap.style.cssText = 'display:flex;gap:6px;align-items:start;margin-bottom:6px;';
 wrap.innerHTML = '<input type="text" style="flex:1;padding:9px 12px;border:1.5px solid #d1d5db;border-radius:5px;font-size:13px;background:#fafbfc;" placeholder="Bold Label \u2013 description text"><button type="button" style="flex-shrink:0;width:28px;height:34px;border:none;background:#ef4444;color:#fff;border-radius:4px;cursor:pointer;font-size:16px;">\u00d7</button>';
 wrap.querySelector('button').onclick = () => wrap.remove();
 list.appendChild(wrap);
 }

 async function handleLetterPDF() {
 const data = gatherLetterData();
 const page = document.getElementById('lt-letter-page');
 if (!page) return;

 // Convert images to data URIs for PDF reliability
 async function imgToDataURI(url) {
 return new Promise(resolve => {
 const img = new Image();
 img.crossOrigin = 'anonymous';
 img.onload = () => { try { const c = document.createElement('canvas'); c.width = img.naturalWidth; c.height = img.naturalHeight; c.getContext('2d').drawImage(img, 0, 0); resolve(c.toDataURL('image/png')); } catch(e) { resolve(url); } };
 img.onerror = () => resolve(url);
 img.src = url;
 });
 }

 const [logoURI, sigURI] = await Promise.all([imgToDataURI(LOGO_SRC), imgToDataURI(SIG_SRC)]);
 page.innerHTML = buildLetterHTML(data, logoURI, sigURI);

 const btn = document.getElementById('lt-btn-pdf');
 if (btn) { btn.disabled = true; btn.textContent = 'GENERATING...'; }

 // Load html2canvas + jsPDF
 async function loadScript(url) {
 if (document.querySelector('script[src="' + url + '"]')) return;
 return new Promise((resolve, reject) => {
 const s = document.createElement('script'); s.src = url;
 s.onload = resolve; s.onerror = () => reject(new Error('Failed: ' + url));
 document.head.appendChild(s);
 });
 }

 try {
 await loadScript('https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js');
 await loadScript('https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js');
 } catch(e) {
 alert('Failed to load PDF libraries. Check your internet connection.');
 if (btn) { btn.disabled = false; btn.textContent = 'DOWNLOAD PDF'; }
 return;
 }

 setTimeout(async () => {
 try {
 const A4_W = 794;
 const contentH = Math.max(1123, page.scrollHeight);
 const canvas = await window.html2canvas(page, {
 scale: 2, useCORS: true, logging: false, letterRendering: true,
 width: A4_W, height: contentH, windowWidth: A4_W, windowHeight: contentH,
 x: 0, y: 0, scrollX: 0, scrollY: 0
 });

 const jsPDF = window.jspdf.jsPDF;
 const pdfW = 210, pdfH = 297;
 const imgW = canvas.width, imgH = canvas.height;
 const pageCanvasH = imgW * (pdfH / pdfW);
 const pages = Math.ceil(imgH / pageCanvasH);
 const pdf = new jsPDF('portrait', 'mm', 'a4');

 for (let p = 0; p < pages; p++) {
 if (p > 0) pdf.addPage();
 const srcY = p * pageCanvasH;
 const sliceH = Math.min(pageCanvasH, imgH - srcY);
 const sliceCanvas = document.createElement('canvas');
 sliceCanvas.width = imgW; sliceCanvas.height = sliceH;
 sliceCanvas.getContext('2d').drawImage(canvas, 0, srcY, imgW, sliceH, 0, 0, imgW, sliceH);
 const sliceData = sliceCanvas.toDataURL('image/jpeg', 0.95);
 const sliceMMH = (sliceH / imgW) * pdfW;
 pdf.addImage(sliceData, 'JPEG', 0, 0, pdfW, sliceMMH);
 }

 const filename = data.subject ? 'Bonnesante_' + data.subject.substring(0,40).replace(/[^a-zA-Z0-9 ]/g,'').replace(/ +/g,'_') + '.pdf' : 'Bonnesante_Letter.pdf';
 pdf.save(filename);
 } catch(e) {
 alert('PDF generation failed: ' + e.message);
 } finally {
 if (btn) { btn.disabled = false; btn.textContent = 'DOWNLOAD PDF'; }
 page.innerHTML = buildLetterHTML(data, LOGO_SRC, SIG_SRC);
 }
 }, 300);
 }

 async function handleShareWhatsApp() {
 const data = gatherLetterData();
 const page = document.getElementById('lt-letter-page');
 if (!page || !page.innerHTML || page.innerHTML.includes('Fill in the form')) {
 alert('Please preview your letter first before sharing.');
 return;
 }
 // Generate PDF blob for sharing
 async function imgToDataURI(url) {
 return new Promise(resolve => {
 const img = new Image();
 img.crossOrigin = 'anonymous';
 img.onload = () => { try { const c = document.createElement('canvas'); c.width = img.naturalWidth; c.height = img.naturalHeight; c.getContext('2d').drawImage(img, 0, 0); resolve(c.toDataURL('image/png')); } catch(e) { resolve(url); } };
 img.onerror = () => resolve(url);
 img.src = url;
 });
 }
 async function loadScript(url) {
 if (document.querySelector('script[src="' + url + '"]')) return;
 return new Promise((resolve, reject) => {
 const s = document.createElement('script'); s.src = url;
 s.onload = resolve; s.onerror = () => reject(new Error('Failed: ' + url));
 document.head.appendChild(s);
 });
 }
 try {
 const [logoURI, sigURI] = await Promise.all([imgToDataURI(LOGO_SRC), imgToDataURI(SIG_SRC)]);
 page.innerHTML = buildLetterHTML(data, logoURI, sigURI);
 await loadScript('https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js');
 await loadScript('https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js');
 const A4_W = 794;
 const contentH = Math.max(1123, page.scrollHeight);
 const canvas = await window.html2canvas(page, { scale: 2, useCORS: true, logging: false, letterRendering: true, width: A4_W, height: contentH, windowWidth: A4_W, windowHeight: contentH });
 const jsPDF = window.jspdf.jsPDF;
 const pdf = new jsPDF('portrait', 'mm', 'a4');
 const imgW = canvas.width, imgH = canvas.height;
 const pageCanvasH = imgW * (297 / 210);
 const pages = Math.ceil(imgH / pageCanvasH);
 for (let p = 0; p < pages; p++) {
 if (p > 0) pdf.addPage();
 const srcY = p * pageCanvasH;
 const sliceH = Math.min(pageCanvasH, imgH - srcY);
 const sc = document.createElement('canvas'); sc.width = imgW; sc.height = sliceH;
 sc.getContext('2d').drawImage(canvas, 0, srcY, imgW, sliceH, 0, 0, imgW, sliceH);
 pdf.addImage(sc.toDataURL('image/jpeg', 0.95), 'JPEG', 0, 0, 210, (sliceH / imgW) * 210);
 }
 const pdfBlob = pdf.output('blob');
 const filename = data.subject ? 'Bonnesante_' + data.subject.substring(0,40).replace(/[^a-zA-Z0-9 ]/g,'').replace(/ +/g,'_') + '.pdf' : 'Bonnesante_Letter.pdf';
 // Try Web Share API (mobile/desktop)
 if (navigator.share && navigator.canShare) {
 const file = new File([pdfBlob], filename, { type: 'application/pdf' });
 if (navigator.canShare({ files: [file] })) {
 await navigator.share({ title: 'Bonnesante Medicals Letter', text: data.subject ? 'RE: ' + data.subject : 'Official Letter from Bonnesante Medicals', files: [file] });
 page.innerHTML = buildLetterHTML(data, LOGO_SRC, SIG_SRC);
 return;
 }
 }
 // Fallback: open WhatsApp with text message (PDF cannot be attached via URL scheme)
 const subject = data.subject || 'Official Letter';
 const whatsappText = encodeURIComponent('Bonnesante Medicals - ' + subject + '\n\nPlease find the attached official letter from Bonnesante Medicals.\n\n(Letter has been downloaded to your device. Please attach the PDF manually.)');
 // Also trigger download so user has the file
 const link = document.createElement('a'); link.href = URL.createObjectURL(pdfBlob); link.download = filename; link.click(); URL.revokeObjectURL(link.href);
 window.open('https://wa.me/?text=' + whatsappText, '_blank');
 page.innerHTML = buildLetterHTML(data, LOGO_SRC, SIG_SRC);
 } catch(e) {
 alert('Share failed: ' + e.message);
 page.innerHTML = buildLetterHTML(data, LOGO_SRC, SIG_SRC);
 }
 }

 async function handleShareGmail() {
 const data = gatherLetterData();
 const page = document.getElementById('lt-letter-page');
 if (!page || !page.innerHTML || page.innerHTML.includes('Fill in the form')) {
 alert('Please preview your letter first before sharing.');
 return;
 }
 async function imgToDataURI(url) {
 return new Promise(resolve => {
 const img = new Image();
 img.crossOrigin = 'anonymous';
 img.onload = () => { try { const c = document.createElement('canvas'); c.width = img.naturalWidth; c.height = img.naturalHeight; c.getContext('2d').drawImage(img, 0, 0); resolve(c.toDataURL('image/png')); } catch(e) { resolve(url); } };
 img.onerror = () => resolve(url);
 img.src = url;
 });
 }
 async function loadScript(url) {
 if (document.querySelector('script[src="' + url + '"]')) return;
 return new Promise((resolve, reject) => {
 const s = document.createElement('script'); s.src = url;
 s.onload = resolve; s.onerror = () => reject(new Error('Failed: ' + url));
 document.head.appendChild(s);
 });
 }
 try {
 const [logoURI, sigURI] = await Promise.all([imgToDataURI(LOGO_SRC), imgToDataURI(SIG_SRC)]);
 page.innerHTML = buildLetterHTML(data, logoURI, sigURI);
 await loadScript('https://cdnjs.cloudflare.com/ajax/libs/html2canvas/1.4.1/html2canvas.min.js');
 await loadScript('https://cdnjs.cloudflare.com/ajax/libs/jspdf/2.5.1/jspdf.umd.min.js');
 const A4_W = 794;
 const contentH = Math.max(1123, page.scrollHeight);
 const canvas = await window.html2canvas(page, { scale: 2, useCORS: true, logging: false, letterRendering: true, width: A4_W, height: contentH, windowWidth: A4_W, windowHeight: contentH });
 const jsPDF = window.jspdf.jsPDF;
 const pdf = new jsPDF('portrait', 'mm', 'a4');
 const imgW = canvas.width, imgH = canvas.height;
 const pageCanvasH = imgW * (297 / 210);
 const pages = Math.ceil(imgH / pageCanvasH);
 for (let p = 0; p < pages; p++) {
 if (p > 0) pdf.addPage();
 const srcY = p * pageCanvasH;
 const sliceH = Math.min(pageCanvasH, imgH - srcY);
 const sc = document.createElement('canvas'); sc.width = imgW; sc.height = sliceH;
 sc.getContext('2d').drawImage(canvas, 0, srcY, imgW, sliceH, 0, 0, imgW, sliceH);
 pdf.addImage(sc.toDataURL('image/jpeg', 0.95), 'JPEG', 0, 0, 210, (sliceH / imgW) * 210);
 }
 const pdfBlob = pdf.output('blob');
 const filename = data.subject ? 'Bonnesante_' + data.subject.substring(0,40).replace(/[^a-zA-Z0-9 ]/g,'').replace(/ +/g,'_') + '.pdf' : 'Bonnesante_Letter.pdf';
 // Try Web Share API first
 if (navigator.share && navigator.canShare) {
 const file = new File([pdfBlob], filename, { type: 'application/pdf' });
 if (navigator.canShare({ files: [file] })) {
 await navigator.share({ title: 'Bonnesante Medicals Letter', text: data.subject ? 'RE: ' + data.subject : 'Official Letter from Bonnesante Medicals', files: [file] });
 page.innerHTML = buildLetterHTML(data, LOGO_SRC, SIG_SRC);
 return;
 }
 }
 // Fallback: download PDF then open Gmail compose
 const link = document.createElement('a'); link.href = URL.createObjectURL(pdfBlob); link.download = filename; link.click(); URL.revokeObjectURL(link.href);
 const subject = encodeURIComponent(data.subject ? 'RE: ' + data.subject : 'Official Letter - Bonnesante Medicals');
 const body = encodeURIComponent('Dear Recipient,\n\nPlease find the attached official letter from Bonnesante Medicals.\n\nKind regards,\n' + (data.sigName || 'Bonnesante Medicals') + '\n' + (data.sigTitle || ''));
 window.open('https://mail.google.com/mail/?view=cm&fs=1&su=' + subject + '&body=' + body, '_blank');
 page.innerHTML = buildLetterHTML(data, LOGO_SRC, SIG_SRC);
 } catch(e) {
 alert('Share failed: ' + e.message);
 page.innerHTML = buildLetterHTML(data, LOGO_SRC, SIG_SRC);
 }
 }

 // Set default date on first render
 setTimeout(() => {
 const dateEl = document.getElementById('lt_date');
 if (dateEl && !dateEl.value) {
 const now = new Date();
 dateEl.value = now.getFullYear() + '-' + String(now.getMonth()+1).padStart(2,'0') + '-' + String(now.getDate()).padStart(2,'0');
 }
 }, 0);

 return (
 <div className="module-content" style={{padding:0, background:'#f4f5f7'}}>
 {/* Letters Header */}
 <div style={{background:'linear-gradient(135deg,#0f2460,#1a3a8a,#3b7ddd)', color:'#fff', padding:'20px 30px', textAlign:'center'}}>
 <h1 style={{fontSize:28, fontWeight:800, letterSpacing:2, margin:0}}>ASTRO LETTERS</h1>
 <p style={{fontSize:13, opacity:0.8, marginTop:4, letterSpacing:1}}>Bonnesante Medicals Official Letterhead Generator</p>
 </div>

 <div style={{maxWidth:1400, margin:'0 auto', padding:20, display:'flex', gap:24, alignItems:'flex-start', flexWrap:'wrap'}}>
 {/* Form Panel */}
 <div style={{flex:'0 0 460px', background:'#fff', borderRadius:8, boxShadow:'0 1px 3px rgba(0,0,0,0.1)', padding:24, maxHeight:'calc(100vh - 180px)', overflowY:'auto', position:'sticky', top:20}}>
 <h2 style={{fontSize:18, fontWeight:700, marginBottom:16, color:'#1a3a8a', borderBottom:'2px solid #1a3a8a', paddingBottom:6}}>Compose Letter</h2>

 {/* Date */}
 <div style={{marginBottom:18}}>
 <h3 style={{fontSize:11, fontWeight:700, textTransform:'uppercase', letterSpacing:1, color:'#6b7280', marginBottom:8}}>Date</h3>
 <input type="date" id="lt_date" style={{width:'100%', padding:'9px 12px', border:'1.5px solid #d1d5db', borderRadius:5, fontSize:13, background:'#fafbfc'}} />
 </div>

 {/* Recipient */}
 <div style={{marginBottom:18}}>
 <h3 style={{fontSize:11, fontWeight:700, textTransform:'uppercase', letterSpacing:1, color:'#6b7280', marginBottom:8}}>Recipient</h3>
 <label style={{display:'block', fontSize:12, fontWeight:600, marginBottom:3, color:'#374151'}}>Title / Designation</label>
 <input type="text" id="lt_recip_title" placeholder="e.g. The Director," style={{width:'100%', padding:'9px 12px', border:'1.5px solid #d1d5db', borderRadius:5, fontSize:13, background:'#fafbfc', marginBottom:10}} />
 <label style={{display:'block', fontSize:12, fontWeight:600, marginBottom:3, color:'#374151'}}>Organisation / Department</label>
 <textarea id="lt_recip_org" rows="3" placeholder={"Drug Evaluation and Research\nNAFDAC Office\nIsolo, Lagos\nNigeria"} style={{width:'100%', padding:'9px 12px', border:'1.5px solid #d1d5db', borderRadius:5, fontSize:13, background:'#fafbfc', marginBottom:10, resize:'vertical', minHeight:100, lineHeight:1.6}} />
 </div>

 {/* Salutation */}
 <div style={{marginBottom:18}}>
 <h3 style={{fontSize:11, fontWeight:700, textTransform:'uppercase', letterSpacing:1, color:'#6b7280', marginBottom:8}}>Salutation</h3>
 <select id="lt_salutation" style={{width:'100%', padding:'9px 12px', border:'1.5px solid #d1d5db', borderRadius:5, fontSize:13, background:'#fafbfc', marginBottom:10}}>
 <option value="Dear Sir">Dear Sir</option>
 <option value="Dear Sir/Madam">Dear Sir/Madam</option>
 <option value="Dear Madam">Dear Madam</option>
 <option value="Dear Dr.">Dear Dr.</option>
 <option value="Dear Prof.">Dear Prof.</option>
 <option value="To Whom It May Concern">To Whom It May Concern</option>
 </select>
 <label style={{display:'block', fontSize:12, fontWeight:600, marginBottom:3, color:'#374151'}}>Custom name (optional)</label>
 <input type="text" id="lt_sal_name" placeholder="Leave blank for generic, or type a name" style={{width:'100%', padding:'9px 12px', border:'1.5px solid #d1d5db', borderRadius:5, fontSize:13, background:'#fafbfc', marginBottom:10}} />
 </div>

 {/* Subject */}
 <div style={{marginBottom:18}}>
 <h3 style={{fontSize:11, fontWeight:700, textTransform:'uppercase', letterSpacing:1, color:'#6b7280', marginBottom:8}}>Subject Line</h3>
 <input type="text" id="lt_subject" placeholder="APPLICATION FOR LAYOUT DESK REVIEW..." style={{width:'100%', padding:'9px 12px', border:'1.5px solid #d1d5db', borderRadius:5, fontSize:13, background:'#fafbfc', marginBottom:10}} />
 </div>

 {/* Body */}
 <div style={{marginBottom:18}}>
 <h3 style={{fontSize:11, fontWeight:700, textTransform:'uppercase', letterSpacing:1, color:'#6b7280', marginBottom:8}}>Letter Body</h3>
 <label style={{display:'block', fontSize:12, fontWeight:600, marginBottom:3, color:'#374151'}}>Opening Paragraph</label>
 <textarea id="lt_body_open" rows="4" placeholder="We write to formally apply for..." style={{width:'100%', padding:'9px 12px', border:'1.5px solid #d1d5db', borderRadius:5, fontSize:13, background:'#fafbfc', marginBottom:10, resize:'vertical', minHeight:100, lineHeight:1.6}} />
 <label style={{display:'block', fontSize:12, fontWeight:600, marginBottom:3, color:'#374151'}}>Middle Paragraph(s)</label>
 <textarea id="lt_body_mid" rows="5" placeholder="Additional details, context, or explanations..." style={{width:'100%', padding:'9px 12px', border:'1.5px solid #d1d5db', borderRadius:5, fontSize:13, background:'#fafbfc', marginBottom:10, resize:'vertical', minHeight:100, lineHeight:1.6}} />
 <label style={{display:'block', fontSize:12, fontWeight:600, marginBottom:3, color:'#374151'}}>Bullet Points (optional)</label>
 <div id="lt-bullet-list"></div>
 <button type="button" onClick={handleAddBullet} style={{background:'none', border:'1px dashed #1a3a8a', color:'#1a3a8a', padding:'6px 12px', borderRadius:4, cursor:'pointer', fontSize:12, fontWeight:600}}>+ Add Bullet Point</button>
 <label style={{display:'block', fontSize:12, fontWeight:600, marginBottom:3, marginTop:10, color:'#374151'}}>Closing Paragraph</label>
 <textarea id="lt_body_close" rows="3" placeholder="We are committed to maintaining the highest standards..." style={{width:'100%', padding:'9px 12px', border:'1.5px solid #d1d5db', borderRadius:5, fontSize:13, background:'#fafbfc', marginBottom:10, resize:'vertical', minHeight:100, lineHeight:1.6}} />
 </div>

 {/* Sign-off */}
 <div style={{marginBottom:18}}>
 <h3 style={{fontSize:11, fontWeight:700, textTransform:'uppercase', letterSpacing:1, color:'#6b7280', marginBottom:8}}>Sign-off</h3>
 <select id="lt_closing" style={{width:'100%', padding:'9px 12px', border:'1.5px solid #d1d5db', borderRadius:5, fontSize:13, background:'#fafbfc', marginBottom:10}}>
 <option value="Yours sincerely,">Yours sincerely,</option>
 <option value="Yours faithfully,">Yours faithfully,</option>
 <option value="Best regards,">Best regards,</option>
 <option value="Kind regards,">Kind regards,</option>
 <option value="Respectfully,">Respectfully,</option>
 <option value="Thank you,">Thank you,</option>
 </select>
 </div>

 {/* Signatory */}
 <div style={{marginBottom:18}}>
 <h3 style={{fontSize:11, fontWeight:700, textTransform:'uppercase', letterSpacing:1, color:'#6b7280', marginBottom:8}}>Signatory</h3>
 <div style={{display:'flex', gap:10}}>
 <div style={{flex:1}}><label style={{display:'block', fontSize:12, fontWeight:600, marginBottom:3, color:'#374151'}}>Full Name</label><input type="text" id="lt_sig_name" defaultValue="Dr. E.C. Nnadi" style={{width:'100%', padding:'9px 12px', border:'1.5px solid #d1d5db', borderRadius:5, fontSize:13, background:'#fafbfc', marginBottom:10}} /></div>
 <div style={{flex:1}}><label style={{display:'block', fontSize:12, fontWeight:600, marginBottom:3, color:'#374151'}}>Title</label><input type="text" id="lt_sig_title" defaultValue="Director, Bonnesante Medicals" style={{width:'100%', padding:'9px 12px', border:'1.5px solid #d1d5db', borderRadius:5, fontSize:13, background:'#fafbfc', marginBottom:10}} /></div>
 </div>
 <div style={{display:'flex', gap:10}}>
 <div style={{flex:1}}><label style={{display:'block', fontSize:12, fontWeight:600, marginBottom:3, color:'#374151'}}>Phone</label><input type="tel" id="lt_sig_phone" defaultValue="08033328385" style={{width:'100%', padding:'9px 12px', border:'1.5px solid #d1d5db', borderRadius:5, fontSize:13, background:'#fafbfc', marginBottom:10}} /></div>
 <div style={{flex:1}}><label style={{display:'block', fontSize:12, fontWeight:600, marginBottom:3, color:'#374151'}}>Email (optional)</label><input type="email" id="lt_sig_email" placeholder="" style={{width:'100%', padding:'9px 12px', border:'1.5px solid #d1d5db', borderRadius:5, fontSize:13, background:'#fafbfc', marginBottom:10}} /></div>
 </div>
 </div>

 {/* Buttons */}
 <div style={{display:'flex', gap:10, marginTop:18, flexWrap:'wrap'}}>
 <button type="button" onClick={handleLetterPreview} style={{flex:1, padding:'12px 16px', border:'none', borderRadius:5, fontSize:14, fontWeight:700, cursor:'pointer', letterSpacing:0.5, textTransform:'uppercase', minWidth:110, background:'#1a3a8a', color:'#fff'}}>Preview</button>
 <button type="button" id="lt-btn-pdf" onClick={handleLetterPDF} style={{flex:1, padding:'12px 16px', border:'none', borderRadius:5, fontSize:14, fontWeight:700, cursor:'pointer', letterSpacing:0.5, textTransform:'uppercase', minWidth:110, background:'#059669', color:'#fff'}}>Download PDF</button>
 <button type="button" onClick={handleLetterClear} style={{flex:1, padding:'12px 16px', border:'none', borderRadius:5, fontSize:14, fontWeight:700, cursor:'pointer', letterSpacing:0.5, textTransform:'uppercase', minWidth:110, background:'#6b7280', color:'#fff'}}>Clear</button>
 </div>
 <div style={{display:'flex', gap:10, marginTop:10, flexWrap:'wrap'}}>
 <button type="button" onClick={handleShareWhatsApp} style={{flex:1, padding:'10px 16px', border:'none', borderRadius:5, fontSize:13, fontWeight:700, cursor:'pointer', letterSpacing:0.5, textTransform:'uppercase', minWidth:110, background:'#25D366', color:'#fff', display:'flex', alignItems:'center', justifyContent:'center', gap:6}}>
 <svg width="18" height="18" viewBox="0 0 24 24" fill="white"><path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/></svg>
 WhatsApp
 </button>
 <button type="button" onClick={handleShareGmail} style={{flex:1, padding:'10px 16px', border:'none', borderRadius:5, fontSize:13, fontWeight:700, cursor:'pointer', letterSpacing:0.5, textTransform:'uppercase', minWidth:110, background:'#EA4335', color:'#fff', display:'flex', alignItems:'center', justifyContent:'center', gap:6}}>
 <svg width="18" height="18" viewBox="0 0 24 24" fill="white"><path d="M24 5.457v13.909c0 .904-.732 1.636-1.636 1.636h-3.819V11.73L12 16.64l-6.545-4.91v9.273H1.636A1.636 1.636 0 010 19.366V5.457c0-2.023 2.309-3.178 3.927-1.964L5.455 4.64 12 9.548l6.545-4.91 1.528-1.145C21.69 2.28 24 3.434 24 5.457z"/></svg>
 Gmail
 </button>
 </div>
 </div>

 {/* Preview Panel */}
 <div style={{flex:1, minWidth:0}}>
 <p style={{fontSize:12, fontWeight:700, textTransform:'uppercase', letterSpacing:1, color:'#6b7280', marginBottom:8}}>Live Preview</p>
 <div style={{background:'#fff', borderRadius:8, boxShadow:'0 2px 10px rgba(0,0,0,0.12)', overflow:'hidden'}}>
 <div id="lt-letter-page" style={{width:794, minHeight:1123, margin:'0 auto', padding:0, fontFamily:'Georgia,serif', color:'#111', background:'#fff', fontSize:'12pt', lineHeight:1.7, display:'flex', flexDirection:'column'}}>
 <div style={{display:'flex', alignItems:'center', justifyContent:'center', minHeight:500, color:'#aaa', fontSize:16, textAlign:'center', padding:40}}>
 Fill in the form and click <strong style={{margin:'0 6px'}}>Preview</strong> to see your letter.
 </div>
 </div>
 </div>
 </div>
 </div>
 </div>
 );
 })()}
 </main>

 {/* Universal Modal */}
 {showForm && (
 <div className="modal-overlay">
 <div className="modal">
 <div className="modal-header">
 <div className="modal-header-left"><img src="/company-logo.png" alt="AstroBSM StockMaster" className="modal-logo" onError={(e) => { e.target.style.display = 'none'; }}/><h3>{editingItem ? 'Edit' : 'Add'} {showForm.replace(/([A-Z])/g,' $1').trim()}</h3></div>
 <button className="modal-close" onClick={() => setShowForm('')}></button>
 </div>
 <div className="modal-content">
 <img src="/company-logo.png" alt="AstroBSM StockMaster" className="form-logo" onError={(e) => { e.target.style.display = 'none'; }} />
 {/* Staff */}
 {showForm === 'staff' && (
 <form onSubmit={(e)=>{e.preventDefault(); saveItem('staff','/api/staff/staffs');}}>
 <div className="form-row">
 <div className="form-group"><label>First Name *</label><input value={forms.staff.first_name} onChange={(e)=>handleFormChange('staff','first_name',e.target.value)} required/></div>
 <div className="form-group"><label>Last Name *</label><input value={forms.staff.last_name} onChange={(e)=>handleFormChange('staff','last_name',e.target.value)} required/></div>
 <div className="form-group"><label>Phone Number</label><input type="tel" value={forms.staff.phone} onChange={(e)=>handleFormChange('staff','phone',e.target.value)}/></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Date of Birth</label><input type="date" value={forms.staff.date_of_birth} onChange={(e)=>handleFormChange('staff','date_of_birth',e.target.value)}/></div>
 <div className="form-group"><label>Position</label><input value={forms.staff.position} onChange={(e)=>handleFormChange('staff','position',e.target.value)}/></div>
 <div className="form-group"><label>Hire Date</label><input type="date" value={forms.staff.hire_date} onChange={(e)=>handleFormChange('staff','hire_date',e.target.value)}/></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Payment Mode *</label><select value={forms.staff.payment_mode} onChange={(e)=>handleFormChange('staff','payment_mode',e.target.value)} required><option value="">Select payment mode</option><option value="monthly">Monthly</option><option value="hourly">Hour Worked (From Timed Attendance)</option></select></div>
 {forms.staff.payment_mode === 'monthly' && (
 <div className="form-group"><label>Monthly Salary (₦) *</label><input type="number" step="0.01" value={forms.staff.monthly_salary} onChange={(e)=>handleFormChange('staff','monthly_salary',e.target.value)} required placeholder="e.g., 150000" /></div>
 )}
 {forms.staff.payment_mode === 'hourly' && (
 <div className="form-group"><label>Hourly Rate (₦) *</label><input type="number" step="0.01" value={forms.staff.hourly_rate} onChange={(e)=>handleFormChange('staff','hourly_rate',e.target.value)} required placeholder="e.g., 2500" /></div>
 )}
 <div className="form-group"><label>Bank Currency</label><input value={forms.staff.bank_currency} onChange={(e)=>handleFormChange('staff','bank_currency',e.target.value)} placeholder="NGN"/></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Bank Name</label><input value={forms.staff.bank_name} onChange={(e)=>handleFormChange('staff','bank_name',e.target.value)}/></div>
 <div className="form-group"><label>Account Number</label><input value={forms.staff.bank_account_number} onChange={(e)=>handleFormChange('staff','bank_account_number',e.target.value)}/></div>
 <div className="form-group"><label>Account Name</label><input value={forms.staff.bank_account_name} onChange={(e)=>handleFormChange('staff','bank_account_name',e.target.value)}/></div>
 </div>
 <div className="info-box">INFO: Employee ID (BSM####) and Clock PIN will be auto-generated</div>
 <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Saving...':(editingItem?'Update':'Add')} Staff</button></div>
 </form>
 )}

 {/* Product */}
 {showForm === 'product' && (
 <form onSubmit={(e)=>{e.preventDefault(); saveItem('product','/api/products/');}}>
 <div className="form-row">
 <div className="form-group"><label>Product Code/SKU *</label><input value={forms.product.sku} onChange={(e)=>handleFormChange('product','sku',e.target.value)} required/></div>
 <div className="form-group"><label>Product Name *</label><input value={forms.product.name} onChange={(e)=>handleFormChange('product','name',e.target.value)} required/></div>
 <div className="form-group"><label>Manufacturer</label><input value={forms.product.manufacturer} onChange={(e)=>handleFormChange('product','manufacturer',e.target.value)}/></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Reorder Level</label><input type="number" step="0.01" value={forms.product.reorder_level} onChange={(e)=>handleFormChange('product','reorder_level',e.target.value)}/></div>
 <div className="form-group"><label>Minimum Order Quantity (MOQ)</label><input type="number" step="0.01" value={forms.product.minimum_order_quantity} onChange={(e)=>handleFormChange('product','minimum_order_quantity',e.target.value)}/></div>
 <div className="form-group"><label>Lead Time (Days)</label><input type="number" value={forms.product.lead_time_days} onChange={(e)=>handleFormChange('product','lead_time_days',e.target.value)}/></div>
 </div>

 <div className="lines-section">
 <div className="lines-header">
 <h4>Units of Measure & Pricing</h4>
 <button type="button" className="btn btn-secondary" onClick={addProductPricing}>Add Unit</button>
 </div>
 {(forms.product.pricing||[]).map((pricing, idx) => (
 <div key={idx} className="form-row line-row">
 <div className="form-group">
 <label>Unit *</label>
 <input 
 value={pricing.unit} 
 onChange={(e)=>updateProductPricing(idx,'unit',e.target.value)} 
 required 
 placeholder="e.g., piece, box, carton"
 />
 </div>
 <div className="form-group">
 <label>Cost Price (₦) *</label>
 <input 
 type="number" 
 step="0.01" 
 value={pricing.cost_price} 
 onChange={(e)=>updateProductPricing(idx,'cost_price',e.target.value)} 
 required
 placeholder="0.00"
 />
 </div>
 <div className="form-group">
 <label>Retail Price (₦) *</label>
 <input 
 type="number" 
 step="0.01" 
 value={pricing.retail_price} 
 onChange={(e)=>updateProductPricing(idx,'retail_price',e.target.value)} 
 required
 placeholder="0.00"
 />
 </div>
 <div className="form-group">
 <label>Wholesale Price (₦) *</label>
 <input 
 type="number" 
 step="0.01" 
 value={pricing.wholesale_price} 
 onChange={(e)=>updateProductPricing(idx,'wholesale_price',e.target.value)} 
 required
 placeholder="0.00"
 />
 </div>
 <div className="form-group">
 <button 
 type="button" 
 className="btn btn-danger" 
 onClick={()=>removeProductPricing(idx)}
 disabled={forms.product.pricing.length === 1}
 style={{marginTop: '1.5rem'}}
 >
 Remove
 </button>
 </div>
 </div>
 ))}
 </div>
 <div className="form-group"><label>Description</label><textarea rows="3" value={forms.product.description} onChange={(e)=>handleFormChange('product','description',e.target.value)}/></div>
 <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Saving...':(editingItem?'Update':'Add')} Product</button></div>
 </form>
 )}

 {/* Raw Material */}
 {showForm === 'rawMaterial' && (
 <form onSubmit={(e)=>{e.preventDefault(); saveItem('rawMaterial','/api/raw-materials/');}}>
 <div className="form-row">
 <div className="form-group"><label>Raw Material Name *</label><input value={forms.rawMaterial.name} onChange={(e)=>handleFormChange('rawMaterial','name',e.target.value)} required placeholder="e.g., Cotton Gauze"/></div>
 <div className="form-group"><label>Raw Material Code / SKU (Auto-generated)</label><input value={forms.rawMaterial.sku} readOnly style={{backgroundColor: '#f0f0f0', cursor: 'default'}} placeholder="Auto-generated from name"/></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Manufacturer / Supplier</label><input value={forms.rawMaterial.manufacturer} onChange={(e)=>handleFormChange('rawMaterial','manufacturer',e.target.value)} placeholder="e.g., ABC Medical Supplies"/></div>
 <div className="form-group"><label>Unit of Measure (UoM) *</label><select value={forms.rawMaterial.unit} onChange={(e)=>handleFormChange('rawMaterial','unit',e.target.value)} required><option value="kg">Kilogram (kg)</option><option value="g">Gram (g)</option><option value="L">Liter (L)</option><option value="mL">Milliliter (mL)</option><option value="m">Meter (m)</option><option value="cm">Centimeter (cm)</option><option value="pcs">Pieces (pcs)</option><option value="box">Box</option><option value="roll">Roll</option><option value="pack">Pack</option></select></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Reorder Level *</label><input type="number" step="0.01" value={forms.rawMaterial.reorder_level} onChange={(e)=>handleFormChange('rawMaterial','reorder_level',e.target.value)} required placeholder="Minimum stock level"/></div>
 <div className="form-group"><label>Cost Price (₦) *</label><input type="number" step="0.01" value={forms.rawMaterial.unit_cost} onChange={(e)=>handleFormChange('rawMaterial','unit_cost',e.target.value)} required placeholder="0.00"/></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Opening Stock</label><input type="number" step="0.01" value={forms.rawMaterial.opening_stock} onChange={(e)=>handleFormChange('rawMaterial','opening_stock',e.target.value)} placeholder="Initial quantity in stock"/></div>
 </div>
 <div className="form-group"><label>Date Added</label><input type="date" value={forms.rawMaterial.date_added} onChange={(e)=>handleFormChange('rawMaterial','date_added',e.target.value)} defaultValue={new Date().toISOString().split('T')[0]}/></div>
 <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Saving...':'Add Raw Material'}</button></div>
 </form>
 )}

 {/* Stock Intake (Product) */}
 {showForm === 'stockIntake' && (
 <form onSubmit={async (e)=>{
 e.preventDefault();
 try {
 if (!forms.stockIntake.unit) throw new Error('Please select a unit');
 setLoading(true);
 const payload = { 
 product_id: forms.stockIntake.product_id, 
 warehouse_id: forms.stockIntake.warehouse_id, 
 quantity: forms.stockIntake.quantity, 
 unit_cost: forms.stockIntake.unit_cost || null, 
 unit: forms.stockIntake.unit,
 supplier: forms.stockIntake.supplier || null,
 batch_number: forms.stockIntake.batch_number || null,
 expiry_date: forms.stockIntake.expiry_date || null,
 notes: forms.stockIntake.notes || null 
 };
 const res = await authedFetch('/api/stock/intake/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
 if (!res.ok) throw new Error((await res.json()).detail || 'Failed');
 notify('Stock intake recorded','success'); setShowForm(''); fetchAllData();
 } catch(e){ notify(`Error: ${e.message}`,'error'); } finally { setLoading(false); }
 }}>
 <div className="form-row">
 <div className="form-group"><label>Product *</label><select value={forms.stockIntake.product_id} onChange={(e)=>{
 const pid = e.target.value;
 handleFormChange('stockIntake','product_id', pid);
 handleFormChange('stockIntake','unit','');
 handleFormChange('stockIntake','unit_cost','');
 }} required><option value="">Select product</option>{(data.products||[]).map(p=>(<option key={p.id} value={p.id}>{p.name}</option>))}</select></div>
 <div className="form-group"><label>Warehouse *</label><select value={forms.stockIntake.warehouse_id} onChange={(e)=>handleFormChange('stockIntake','warehouse_id',e.target.value)} required><option value="">Select warehouse</option>{(data.warehouses||[]).map(w=>(<option key={w.id} value={w.id}>{w.name}</option>))}</select></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Unit *</label>
 {(() => {
 const selectedProd = (data.products||[]).find(p=>p.id===forms.stockIntake.product_id);
 const units = selectedProd?.pricing?.map(pr=>pr.unit) || [];
 return (
 <select value={forms.stockIntake.unit} onChange={(e)=>{
 const selUnit = e.target.value;
 handleFormChange('stockIntake','unit', selUnit);
 const prEntry = selectedProd?.pricing?.find(pr=>pr.unit===selUnit);
 if(prEntry) handleFormChange('stockIntake','unit_cost', String(prEntry.cost_price||''));
 }} required>
 <option value="">{forms.stockIntake.product_id ? (units.length ? 'Select unit' : 'No units configured') : 'Select product first'}</option>
 {units.map(u => <option key={u} value={u}>{u}</option>)}
 </select>
 );
 })()}
 </div>
 <div className="form-group"><label>Quantity * <span style={{fontSize:11,color:'#667eea',fontWeight:600}}>{forms.stockIntake.unit ? `(in ${forms.stockIntake.unit})` : ''}</span></label><input type="number" value={forms.stockIntake.quantity} onChange={(e)=>handleFormChange('stockIntake','quantity',e.target.value)} required placeholder={forms.stockIntake.unit ? `Enter qty in ${forms.stockIntake.unit}` : 'Enter quantity'}/></div>
 <div className="form-group"><label>Unit Cost</label><input type="number" step="0.01" value={forms.stockIntake.unit_cost} onChange={(e)=>handleFormChange('stockIntake','unit_cost',e.target.value)}/></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Supplier</label><input value={forms.stockIntake.supplier} onChange={(e)=>handleFormChange('stockIntake','supplier',e.target.value)}/></div>
 <div className="form-group"><label>Batch Number</label><input value={forms.stockIntake.batch_number} onChange={(e)=>handleFormChange('stockIntake','batch_number',e.target.value)}/></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Expiry Date</label><input type="date" value={forms.stockIntake.expiry_date} onChange={(e)=>handleFormChange('stockIntake','expiry_date',e.target.value)}/></div>
 <div className="form-group"><label>Notes</label><input value={forms.stockIntake.notes} onChange={(e)=>handleFormChange('stockIntake','notes',e.target.value)}/></div>
 </div>
 <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Saving...':'Record Intake'}</button></div>
 </form>
 )}

 {/* Raw Material Intake */}
 {showForm === 'rawMaterialIntake' && (
 <form onSubmit={async (e)=>{
 e.preventDefault();
 try {
 setLoading(true);
 const payload = { 
 raw_material_id: forms.rawMaterialIntake.raw_material_id, 
 warehouse_id: forms.rawMaterialIntake.warehouse_id, 
 quantity: forms.rawMaterialIntake.quantity, 
 unit_cost: forms.rawMaterialIntake.unit_cost || null, 
 supplier: forms.rawMaterialIntake.supplier || null,
 batch_number: forms.rawMaterialIntake.batch_number || null,
 expiry_date: forms.rawMaterialIntake.expiry_date || null,
 notes: forms.rawMaterialIntake.notes || null 
 };
 const res = await authedFetch('/api/stock/intake/raw-material/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
 if (!res.ok) throw new Error((await res.json()).detail || 'Failed');
 notify('Raw material intake recorded','success'); setShowForm(''); fetchAllData();
 } catch(e){ notify(`Error: ${e.message}`,'error'); } finally { setLoading(false); }
 }}>
 <div className="form-row">
 <div className="form-group"><label>Raw Material *</label><select value={forms.rawMaterialIntake.raw_material_id} onChange={(e)=>handleFormChange('rawMaterialIntake','raw_material_id',e.target.value)} required><option value="">Select raw material</option>{(data.rawMaterials||[]).map(r=>(<option key={r.id} value={r.id}>{r.name}</option>))}</select></div>
 <div className="form-group"><label>Warehouse *</label><select value={forms.rawMaterialIntake.warehouse_id} onChange={(e)=>handleFormChange('rawMaterialIntake','warehouse_id',e.target.value)} required><option value="">Select warehouse</option>{(data.warehouses||[]).map(w=>(<option key={w.id} value={w.id}>{w.name}</option>))}</select></div>
 <div className="form-group"><label>Quantity *</label><input type="number" value={forms.rawMaterialIntake.quantity} onChange={(e)=>handleFormChange('rawMaterialIntake','quantity',e.target.value)} required/></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Unit Cost</label><input type="number" step="0.01" value={forms.rawMaterialIntake.unit_cost} onChange={(e)=>handleFormChange('rawMaterialIntake','unit_cost',e.target.value)}/></div>
 <div className="form-group"><label>Supplier</label><input value={forms.rawMaterialIntake.supplier} onChange={(e)=>handleFormChange('rawMaterialIntake','supplier',e.target.value)}/></div>
 <div className="form-group"><label>Batch Number</label><input value={forms.rawMaterialIntake.batch_number} onChange={(e)=>handleFormChange('rawMaterialIntake','batch_number',e.target.value)}/></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Expiry Date</label><input type="date" value={forms.rawMaterialIntake.expiry_date} onChange={(e)=>handleFormChange('rawMaterialIntake','expiry_date',e.target.value)}/></div>
 <div className="form-group"><label>Notes</label><input value={forms.rawMaterialIntake.notes} onChange={(e)=>handleFormChange('rawMaterialIntake','notes',e.target.value)}/></div>
 </div>
 <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Saving...':'Record Intake'}</button></div>
 </form>
 )}

 {/* Warehouse */}
 {showForm === 'warehouse' && (
 <form onSubmit={(e)=>{e.preventDefault(); saveItem('warehouse','/api/warehouses/');}}>
 <div className="form-row">
 <div className="form-group"><label>Code *</label><input value={forms.warehouse.code} onChange={(e)=>handleFormChange('warehouse','code',e.target.value)} required placeholder="e.g., WH-001"/></div>
 <div className="form-group"><label>Name *</label><input value={forms.warehouse.name} onChange={(e)=>handleFormChange('warehouse','name',e.target.value)} required placeholder="e.g., Main Warehouse"/></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Location</label><input value={forms.warehouse.location} onChange={(e)=>handleFormChange('warehouse','location',e.target.value)} placeholder="e.g., Lagos, Nigeria"/></div>
 <div className="form-group">
 <label>Warehouse Manager</label>
 <select value={forms.warehouse.manager_id} onChange={(e)=>handleFormChange('warehouse','manager_id',e.target.value)}>
 <option value="">No Manager Assigned</option>
 {(data.staff||[]).map(s=>(<option key={s.id} value={s.id}>{s.first_name} {s.last_name} - {s.position || 'Staff'}</option>))}
 </select>
 </div>
 </div>
 <div className="info-box">
 INFO: Warehouse manager can be assigned to oversee operations. This can be edited later by admins.
 </div>
 <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Saving...':(editingItem?'Update':'Add')+' Warehouse'}</button></div>
 </form>
 )}

 {/* Customer */}
 {showForm === 'customer' && (
 <form onSubmit={(e)=>{e.preventDefault(); saveItem('customer','/api/sales/customers');}}>
 <div className="form-row">
 <div className="form-group">
 <label>Customer Code</label>
 <input 
 value={forms.customer.customer_code || ''} 
 onChange={(e)=>handleFormChange('customer','customer_code',e.target.value)} 
 placeholder="Auto-generated (e.g., CUST-0001)"
 maxLength="32"
 style={{background:'#f8f9fa',color:'#6c757d',fontStyle: forms.customer.customer_code ? 'normal' : 'italic'}}
 />
 <small style={{color:'#888',fontSize:11}}>Leave blank for auto-generation</small>
 </div>
 <div className="form-group">
 <label>Customer Name *</label>
 <input 
 value={forms.customer.name} 
 onChange={(e)=>handleFormChange('customer','name',e.target.value)} 
 required 
 placeholder="Company or Individual Name"
 maxLength="255"
 />
 </div>
 </div>
 <div className="form-row">
 <div className="form-group">
 <label>Email</label>
 <input 
 type="email"
 value={forms.customer.email} 
 onChange={(e)=>handleFormChange('customer','email',e.target.value)} 
 placeholder="customer@example.com"
 maxLength="255"
 />
 </div>
 <div className="form-group">
 <label>Phone Number</label>
 <input 
 type="tel"
 value={forms.customer.phone} 
 onChange={(e)=>handleFormChange('customer','phone',e.target.value)} 
 placeholder="08012345678"
 maxLength="50"
 />
 </div>
 </div>
 <div className="form-row">
 <div className="form-group">
 <label>Address</label>
 <textarea 
 rows="3"
 value={forms.customer.address} 
 onChange={(e)=>handleFormChange('customer','address',e.target.value)} 
 placeholder="Full business or residential address"
 />
 </div>
 </div>
 <div className="form-row">
 <div className="form-group">
 <label>Credit Limit (₦)</label>
 <input 
 type="number"
 step="0.01"
 min="0"
 value={forms.customer.credit_limit} 
 onChange={(e)=>handleFormChange('customer','credit_limit',e.target.value)} 
 placeholder="0.00"
 />
 <small style={{color: 'var(--gray-600)', fontSize: '0.85rem', marginTop: '0.25rem', display: 'block'}}>
 Maximum amount customer can purchase on credit
 </small>
 </div>
 </div>
 <div className="info-box">
 INFO: Customer code should be unique. Email and phone help with communication and reporting.
 </div>
 <div className="modal-actions">
 <button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button>
 <button type="submit" className="btn btn-primary" disabled={loading}>
 {loading ? 'Saving...' : (editingItem ? 'Update' : 'Add')} Customer
 </button>
 </div>
 </form>
 )}

 {/* Payroll */}
 {showForm === 'payroll' && (
 <form onSubmit={submitPayroll}>
 <div className="form-row">
 <div className="form-group"><label>Staff *</label><select value={forms.payroll.staff_id} onChange={(e)=>handleFormChange('payroll','staff_id',e.target.value)} required><option value="">Select staff</option>{(data.staff||[]).map(s=>(<option key={s.id} value={s.id}>{s.first_name} {s.last_name}</option>))}</select></div>
 <div className="form-group"><label>Period Start *</label><input type="date" value={forms.payroll.pay_period_start} onChange={(e)=>handleFormChange('payroll','pay_period_start',e.target.value)} required/></div>
 <div className="form-group"><label>Period End *</label><input type="date" value={forms.payroll.pay_period_end} onChange={(e)=>handleFormChange('payroll','pay_period_end',e.target.value)} required/></div>
 </div>
 <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Processing...':'Process Payroll'}</button></div>
 </form>
 )}

 {/* Sales Order */}
 {showForm === 'salesOrder' && (
 <form onSubmit={submitSalesOrder}>
 <div className="form-row">
 <div className="form-group" style={{position:'relative'}}>
 <label>Customer *</label>
 <input
 type="text"
 placeholder="Search customer by name..."
 value={forms.salesOrder._customerSearch !== undefined ? forms.salesOrder._customerSearch : ((data.customers||[]).find(c=>c.id===forms.salesOrder.customer_id)||{}).name || ''}
 onChange={(e)=>{
 handleFormChange('salesOrder','_customerSearch',e.target.value);
 handleFormChange('salesOrder','_customerDropdownOpen',true);
 if(!e.target.value) handleFormChange('salesOrder','customer_id','');
 }}
 onFocus={()=>handleFormChange('salesOrder','_customerDropdownOpen',true)}
 autoComplete="off"
 required={!forms.salesOrder.customer_id}
 style={{width:'100%'}}
 />
 {forms.salesOrder.customer_id && (
 <span
 onClick={()=>{handleFormChange('salesOrder','customer_id','');handleFormChange('salesOrder','_customerSearch','');setCustomerUnpaidOrders(null);}}
 style={{position:'absolute',right:10,top:'2.4rem',cursor:'pointer',color:'#999',fontSize:16,fontWeight:'bold',lineHeight:1}}
 title="Clear selection"
 >&times;</span>
 )}
 {forms.salesOrder._customerDropdownOpen && (
 <div style={{position:'absolute',top:'100%',left:0,right:0,zIndex:1000,background:'#fff',border:'1px solid #ccc',borderRadius:4,maxHeight:200,overflowY:'auto',boxShadow:'0 4px 12px rgba(0,0,0,0.15)'}}>
 {(data.customers||[])
 .filter(c => {
 const search = (forms.salesOrder._customerSearch||'').toLowerCase();
 return !search || c.name.toLowerCase().includes(search);
 })
 .length === 0 ? (
 <div style={{padding:'8px 12px',color:'#999',fontSize:13}}>No customers found</div>
 ) : (
 (data.customers||[])
 .filter(c => {
 const search = (forms.salesOrder._customerSearch||'').toLowerCase();
 return !search || c.name.toLowerCase().includes(search);
 })
 .map(c=>(
 <div
 key={c.id}
 onClick={()=>{
 handleFormChange('salesOrder','customer_id',c.id);
 handleFormChange('salesOrder','_customerSearch',c.name);
 handleFormChange('salesOrder','_customerDropdownOpen',false);
 fetchCustomerUnpaidOrders(c.id);
 }}
 style={{padding:'8px 12px',cursor:'pointer',fontSize:14,borderBottom:'1px solid #f0f0f0',background:forms.salesOrder.customer_id===c.id?'#e8f0fe':'transparent'}}
 onMouseEnter={(e)=>e.target.style.background='#f5f5f5'}
 onMouseLeave={(e)=>e.target.style.background=forms.salesOrder.customer_id===c.id?'#e8f0fe':'transparent'}
 >{c.name}</div>
 ))
 )
 }
 </div>
 )}
 {/* Click-outside handler */}
 {forms.salesOrder._customerDropdownOpen && (
 <div
 style={{position:'fixed',top:0,left:0,right:0,bottom:0,zIndex:999}}
 onClick={()=>handleFormChange('salesOrder','_customerDropdownOpen',false)}
 />
 )}
 </div>
 <div className="form-group"><button type="button" className="btn btn-secondary" onClick={()=>openForm('customer')} style={{marginTop:'1.5rem'}}>New Customer</button></div>
 </div>

 {/* Customer Unpaid Orders Alert */}
 {customerUnpaidOrders && (
 <div style={{background:'#fff3cd',border:'1px solid #ffc107',borderRadius:8,padding:'12px 16px',marginBottom:12}}>
 <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:8}}>
 <strong style={{color:'#856404',fontSize:15}}>Outstanding Balance for {customerUnpaidOrders.customer_name}</strong>
 <span style={{background:'#dc3545',color:'#fff',padding:'4px 12px',borderRadius:20,fontWeight:'bold',fontSize:14}}>
 {formatCurrency(customerUnpaidOrders.total_outstanding)}
 </span>
 </div>
 <p style={{color:'#856404',margin:'0 0 8px',fontSize:13}}>
 This customer has {customerUnpaidOrders.count} unpaid transaction{customerUnpaidOrders.count > 1 ? 's' : ''}. Details below:
 </p>
 <div style={{maxHeight:200,overflowY:'auto'}}>
 {customerUnpaidOrders.unpaid_orders.map((order, oidx) => (
 <div key={order.order_id} style={{background:'#fff',border:'1px solid #ffe0b2',borderRadius:6,padding:'8px 12px',marginBottom:6}}>
 <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:4}}>
 <span style={{fontWeight:'bold',color:'#333',fontSize:13}}>
 {order.order_number}
 </span>
 <span style={{fontSize:12,color:'#666'}}>
 {order.order_date ? new Date(order.order_date).toLocaleDateString('en-GB',{day:'numeric',month:'short',year:'numeric'}) : ''}
 </span>
 <span style={{fontWeight:'bold',color:'#c0392b',fontSize:13}}>
 {formatCurrency(order.total_amount)}
 </span>
 <span style={{background:order.payment_status==='partial'?'#e67e22':'#e74c3c',color:'#fff',padding:'2px 8px',borderRadius:10,fontSize:11,textTransform:'uppercase'}}>
 {order.payment_status}
 </span>
 </div>
 <div style={{fontSize:12,color:'#555'}}>
 {order.lines.map((line, lidx) => (
 <div key={lidx} style={{display:'flex',justifyContent:'space-between',padding:'1px 0',borderBottom:lidx < order.lines.length-1 ? '1px dotted #eee' : 'none'}}>
 <span>{line.product_name} x{line.quantity}</span>
 <span>{formatCurrency(line.line_total)}</span>
 </div>
 ))}
 </div>
 </div>
 ))}
 </div>
 <div style={{borderTop:'2px solid #ffc107',paddingTop:8,marginTop:8,display:'flex',justifyContent:'space-between',alignItems:'center'}}>
 <strong style={{color:'#856404'}}>Total Outstanding Debt:</strong>
 <strong style={{color:'#dc3545',fontSize:16}}>{formatCurrency(customerUnpaidOrders.total_outstanding)}</strong>
 </div>
 </div>
 )}

 <div className="form-row">
 <div className="form-group">
 <label>Warehouse * <span style={{fontSize:10,color:'#888'}}>(Sales orders use Sales Warehouse only)</span></label>
 <select
 value={forms.salesOrder.warehouse_id}
 onChange={(e)=>handleFormChange('salesOrder','warehouse_id',e.target.value)}
 required
 disabled={!accessibleWarehouses.length}
 >
 <option value="">{accessibleWarehouses.length ? 'Select warehouse' : 'No warehouses assigned'}</option>
 {accessibleWarehouses.filter(w => {
 // Non-admin can only create sales orders from Sales Warehouse
 if (currentUser && currentUser.role === 'admin') return true;
 return w.name && w.name.toUpperCase().includes('SALES');
 }).map((w)=>(<option key={w.id} value={w.id}>{w.name}</option>))}
 </select>
 {!accessibleWarehouses.length && (
 <small style={{ color: 'var(--danger)', display: 'block', marginTop: '0.5rem' }}>
 Warehouse access not granted. Contact an administrator.
 </small>
 )}
 </div>
 <div className="form-group"><label>Order Type *</label><select value={forms.salesOrder.order_type} onChange={(e)=>handleFormChange('salesOrder','order_type',e.target.value)} required><option value="retail">Retail (Use Retail Pricing)</option><option value="wholesale">Wholesale (Use Wholesale Pricing)</option></select></div>
 <div className="form-group"><label>Payment Status *</label><select value={forms.salesOrder.payment_status} onChange={(e)=>handleFormChange('salesOrder','payment_status',e.target.value)} required><option value="unpaid">Unpaid (Generate Invoice)</option><option value="paid">Paid (Generate Receipt)</option></select></div>
 <div className="form-group"><label>Required Date</label><input type="date" value={forms.salesOrder.required_date} onChange={(e)=>handleFormChange('salesOrder','required_date',e.target.value)}/></div>
 </div>
 <div className="form-group"><label>Notes</label><textarea rows="2" value={forms.salesOrder.notes} onChange={(e)=>handleFormChange('salesOrder','notes',e.target.value)}/></div>

 <div className="lines-section">
 <div className="lines-header">
 <h4>Order Lines</h4>
 <button type="button" className="btn btn-secondary" onClick={addSalesLine}>+ Add Line</button>
 </div>
 {(forms.salesOrder.lines||[]).map((line, idx) => (
 <div key={idx} className="form-row line-row">
 <div className="form-group"><label>Product *</label><select value={line.product_id} onChange={(e)=>updateSalesLine(idx,'product_id',e.target.value)} required><option value="">Select product</option>{(data.products||[]).map(p=>(<option key={p.id} value={p.id}>{p.name}</option>))}</select></div>
 <div className="form-group"><label>Unit *</label><select value={line.unit} onChange={(e)=>updateSalesLine(idx,'unit',e.target.value)} required><option value="">Select unit</option>{(() => { const product = (data.products||[]).find(p => p.id === line.product_id); const units = (product && product.pricing && product.pricing.length > 0) ? product.pricing.map(pr => pr.unit) : (product ? ['each'] : []); return units.map((u, pIdx) => (<option key={pIdx} value={u}>{u}</option>)); })()}</select></div>
 <div className="form-group"><label>Qty *</label><input type="number" value={line.quantity} onChange={(e)=>updateSalesLine(idx,'quantity',e.target.value)} required/></div>
 <div className="form-group"><label>Unit Price *</label><input type="number" step="0.01" value={line.unit_price} onChange={(e)=>updateSalesLine(idx,'unit_price',e.target.value)} required/></div>
 <div className="form-group"><button type="button" className="btn btn-danger" onClick={()=>removeSalesLine(idx)}>Delete</button></div>
 </div>
 ))}
 </div>

 <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Saving...':'Create Order'}</button></div>
 </form>
 )}

 {/* Product Stock Intake */}
 {showForm === 'productStockIntake' && (
 <form onSubmit={submitProductStockIntake}>
 <div className="form-row">
 <div className="form-group"><label>Product *</label><select value={forms.stockIntake.product_id} onChange={(e)=>{
 const pid = e.target.value;
 handleFormChange('stockIntake','product_id', pid);
 handleFormChange('stockIntake','unit','');
 handleFormChange('stockIntake','unit_cost','');
 }} required><option value="">Select product</option>{(data.products||[]).map(p=>(<option key={p.id} value={p.id}>{p.name} - {p.sku}</option>))}</select></div>
 <div className="form-group"><label>Warehouse *</label><select value={forms.stockIntake.warehouse_id} onChange={(e)=>handleFormChange('stockIntake','warehouse_id',e.target.value)} required><option value="">Select warehouse</option>{(data.warehouses||[]).map(w=>(<option key={w.id} value={w.id}>{w.name}</option>))}</select></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Unit *</label>
 {(() => {
 const selectedProd = (data.products||[]).find(p=>p.id===forms.stockIntake.product_id);
 const units = selectedProd?.pricing?.map(pr=>pr.unit) || [];
 return (
 <select value={forms.stockIntake.unit} onChange={(e)=>{
 const selUnit = e.target.value;
 handleFormChange('stockIntake','unit', selUnit);
 const prEntry = selectedProd?.pricing?.find(pr=>pr.unit===selUnit);
 if(prEntry) handleFormChange('stockIntake','unit_cost', String(prEntry.cost_price||''));
 }} required>
 <option value="">{forms.stockIntake.product_id ? (units.length ? 'Select unit' : 'No units configured') : 'Select product first'}</option>
 {units.map(u => <option key={u} value={u}>{u}</option>)}
 </select>
 );
 })()}
 </div>
 <div className="form-group"><label>Quantity * <span style={{fontSize:11,color:'#667eea',fontWeight:600}}>{forms.stockIntake.unit ? `(in ${forms.stockIntake.unit})` : ''}</span></label><input type="number" step="0.01" value={forms.stockIntake.quantity} onChange={(e)=>handleFormChange('stockIntake','quantity',e.target.value)} required placeholder={forms.stockIntake.unit ? `Enter qty in ${forms.stockIntake.unit}` : 'Enter quantity'}/></div>
 <div className="form-group"><label>Unit Cost</label><input type="number" step="0.01" value={forms.stockIntake.unit_cost} onChange={(e)=>handleFormChange('stockIntake','unit_cost',e.target.value)}/></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Supplier</label><input value={forms.stockIntake.supplier} onChange={(e)=>handleFormChange('stockIntake','supplier',e.target.value)}/></div>
 <div className="form-group"><label>Batch Number</label><input value={forms.stockIntake.batch_number} onChange={(e)=>handleFormChange('stockIntake','batch_number',e.target.value)}/></div>
 </div>
 <div className="form-group"><label>Notes</label><textarea rows="2" value={forms.stockIntake.notes} onChange={(e)=>handleFormChange('stockIntake','notes',e.target.value)}/></div>
 <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Recording...':'Record Stock Intake'}</button></div>
 </form>
 )}

 {/* Raw Material Stock Intake */}
 {showForm === 'rawMaterialStockIntake' && (
 <form onSubmit={submitRawMaterialStockIntake}>
 <div className="form-row">
 <div className="form-group"><label>Warehouse *</label><select value={forms.rawMaterialIntake.warehouse_id} onChange={(e)=>handleFormChange('rawMaterialIntake','warehouse_id',e.target.value)} required><option value="">Select warehouse</option>{(data.warehouses||[]).map(w=>(<option key={w.id} value={w.id}>{w.name}</option>))}</select></div>
 <div className="form-group"><label>Raw Material *</label><select value={forms.rawMaterialIntake.raw_material_id} onChange={(e)=>handleFormChange('rawMaterialIntake','raw_material_id',e.target.value)} required><option value="">Select raw material</option>{(data.rawMaterials||[]).map(r=>(<option key={r.id} value={r.id}>{r.name} - {r.sku}</option>))}</select></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Quantity *</label><input type="number" step="0.01" value={forms.rawMaterialIntake.quantity} onChange={(e)=>handleFormChange('rawMaterialIntake','quantity',e.target.value)} required/></div>
 <div className="form-group"><label>Unit Cost (₦) *</label><input type="number" step="0.01" value={forms.rawMaterialIntake.unit_cost} onChange={(e)=>handleFormChange('rawMaterialIntake','unit_cost',e.target.value)} required/></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Supplier</label><input value={forms.rawMaterialIntake.supplier} onChange={(e)=>handleFormChange('rawMaterialIntake','supplier',e.target.value)}/></div>
 <div className="form-group"><label>Batch Number</label><input value={forms.rawMaterialIntake.batch_number} onChange={(e)=>handleFormChange('rawMaterialIntake','batch_number',e.target.value)}/></div>
 </div>
 <div className="form-group"><label>Notes</label><textarea rows="2" value={forms.rawMaterialIntake.notes} onChange={(e)=>handleFormChange('rawMaterialIntake','notes',e.target.value)}/></div>
 <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Recording...':'Record Raw Material Intake'}</button></div>
 </form>
 )}

 {/* Damaged Product */}
 {showForm === 'damagedProduct' && (
 <form onSubmit={submitDamagedProduct}>
 <div className="form-row">
 <div className="form-group"><label>Warehouse *</label><select value={forms.damagedProduct.warehouse_id} onChange={(e)=>handleFormChange('damagedProduct','warehouse_id',e.target.value)} required><option value="">Select warehouse</option>{(data.warehouses||[]).map(w=>(<option key={w.id} value={w.id}>{w.name}</option>))}</select></div>
 <div className="form-group"><label>Product *</label><select value={forms.damagedProduct.product_id} onChange={(e)=>handleFormChange('damagedProduct','product_id',e.target.value)} required><option value="">Select product</option>{(data.products||[]).map(p=>(<option key={p.id} value={p.id}>{p.name} - {p.sku}</option>))}</select></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Quantity *</label><input type="number" step="0.01" value={forms.damagedProduct.quantity} onChange={(e)=>handleFormChange('damagedProduct','quantity',e.target.value)} required/></div>
 <div className="form-group"><label>Damage Type *</label><select value={forms.damagedProduct.damage_type} onChange={(e)=>handleFormChange('damagedProduct','damage_type',e.target.value)} required><option value="">Select type</option><option value="Physical Damage">Physical Damage</option><option value="Expired">Expired</option><option value="Defective">Defective</option><option value="Water Damage">Water Damage</option><option value="Other">Other</option></select></div>
 </div>
 <div className="form-group"><label>Damage Reason</label><textarea rows="2" value={forms.damagedProduct.damage_reason} onChange={(e)=>handleFormChange('damagedProduct','damage_reason',e.target.value)} placeholder="Describe the damage"/></div>
 <div className="form-group"><label>Additional Notes</label><textarea rows="2" value={forms.damagedProduct.notes} onChange={(e)=>handleFormChange('damagedProduct','notes',e.target.value)}/></div>
 <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Recording...':'Record Damaged Product'}</button></div>
 </form>
 )}

 {/* Damaged Raw Material */}
 {showForm === 'damagedRawMaterial' && (
 <form onSubmit={submitDamagedRawMaterial}>
 <div className="form-row">
 <div className="form-group"><label>Warehouse *</label><select value={forms.damagedRawMaterial.warehouse_id} onChange={(e)=>handleFormChange('damagedRawMaterial','warehouse_id',e.target.value)} required><option value="">Select warehouse</option>{(data.warehouses||[]).map(w=>(<option key={w.id} value={w.id}>{w.name}</option>))}</select></div>
 <div className="form-group"><label>Raw Material *</label><select value={forms.damagedRawMaterial.raw_material_id} onChange={(e)=>handleFormChange('damagedRawMaterial','raw_material_id',e.target.value)} required><option value="">Select raw material</option>{(data.rawMaterials||[]).map(r=>(<option key={r.id} value={r.id}>{r.name} - {r.sku}</option>))}</select></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Quantity *</label><input type="number" step="0.01" value={forms.damagedRawMaterial.quantity} onChange={(e)=>handleFormChange('damagedRawMaterial','quantity',e.target.value)} required/></div>
 <div className="form-group"><label>Damage Type *</label><select value={forms.damagedRawMaterial.damage_type} onChange={(e)=>handleFormChange('damagedRawMaterial','damage_type',e.target.value)} required><option value="">Select type</option><option value="Physical Damage">Physical Damage</option><option value="Expired">Expired</option><option value="Contaminated">Contaminated</option><option value="Spoiled">Spoiled</option><option value="Other">Other</option></select></div>
 </div>
 <div className="form-group"><label>Damage Reason</label><textarea rows="2" value={forms.damagedRawMaterial.damage_reason} onChange={(e)=>handleFormChange('damagedRawMaterial','damage_reason',e.target.value)} placeholder="Describe the damage"/></div>
 <div className="form-group"><label>Additional Notes</label><textarea rows="2" value={forms.damagedRawMaterial.notes} onChange={(e)=>handleFormChange('damagedRawMaterial','notes',e.target.value)}/></div>
 <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Recording...':'Record Damaged Raw Material'}</button></div>
 </form>
 )}

 {/* Returned Product */}
 {showForm === 'returnedProduct' && (
 <form onSubmit={submitReturnedProduct}>
 <div className="form-row">
 <div className="form-group"><label>Warehouse *</label><select value={forms.returnedProduct.warehouse_id} onChange={(e)=>handleFormChange('returnedProduct','warehouse_id',e.target.value)} required><option value="">Select warehouse</option>{(data.warehouses||[]).map(w=>(<option key={w.id} value={w.id}>{w.name}</option>))}</select></div>
 <div className="form-group"><label>Product *</label><select value={forms.returnedProduct.product_id} onChange={(e)=>handleFormChange('returnedProduct','product_id',e.target.value)} required><option value="">Select product</option>{(data.products||[]).map(p=>(<option key={p.id} value={p.id}>{p.name} - {p.sku}</option>))}</select></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Quantity *</label><input type="number" step="0.01" value={forms.returnedProduct.quantity} onChange={(e)=>handleFormChange('returnedProduct','quantity',e.target.value)} required/></div>
 <div className="form-group"><label>Return Condition *</label><select value={forms.returnedProduct.return_condition} onChange={(e)=>handleFormChange('returnedProduct','return_condition',e.target.value)} required><option value="good">Good (Resaleable)</option><option value="damaged">Damaged</option><option value="expired">Expired</option></select></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Customer Name</label><input value={forms.returnedProduct.customer_name} onChange={(e)=>handleFormChange('returnedProduct','customer_name',e.target.value)}/></div>
 <div className="form-group"><label>Refund Amount (₦)</label><input type="number" step="0.01" value={forms.returnedProduct.refund_amount} onChange={(e)=>handleFormChange('returnedProduct','refund_amount',e.target.value)}/></div>
 </div>
 <div className="form-group"><label>Return Reason *</label><textarea rows="2" value={forms.returnedProduct.return_reason} onChange={(e)=>handleFormChange('returnedProduct','return_reason',e.target.value)} placeholder="Why is the customer returning this?" required/></div>
 <div className="form-group"><label>Additional Notes</label><textarea rows="2" value={forms.returnedProduct.notes} onChange={(e)=>handleFormChange('returnedProduct','notes',e.target.value)}/></div>
 <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Recording...':'Record Product Return'}</button></div>
 </form>
 )}

 {/* Production Order */}
 {showForm === 'bom' && (
 <form onSubmit={(e) => { e.preventDefault(); saveBOM(); }}>
 <div className="form-group">
 <label>Product *</label>
 {editingBomProductId ? (
 <>
 <input type="text" value={(data.products||[]).find(p => p.id === editingBomProductId)?.name + ' - ' + ((data.products||[]).find(p => p.id === editingBomProductId)?.sku || '')} disabled style={{background:'#f0f0f0'}} />
 <small style={{color:'#888'}}>Product cannot be changed when editing. To change product, delete this BOM and create a new one.</small>
 </>
 ) : (
 <select 
 value={forms.bom.product_id} 
 onChange={(e) => handleFormChange('bom', 'product_id', e.target.value)}
 required
 >
 <option value="">Select Product</option>
 {(data.products||[]).map(p => (
 <option key={p.id} value={p.id}>{p.name} - {p.sku}</option>
 ))}
 </select>
 )}
 </div>
 
 <div className="bom-lines-section">
 <h4>Raw Material Lines</h4>
 {bomLines.map((line) => (
 <div key={line.id} className="bom-line">
 <div className="form-row">
 <div className="form-group" style={{flex: '2'}}>
 <label>Raw Material *</label>
 <select 
 value={line.raw_material_id} 
 onChange={(e) => updateBomLine(line.id, 'raw_material_id', e.target.value)}
 required
 >
 <option value="">Select Raw Material</option>
 {(data.rawMaterials||[]).map(rm => (
 <option key={rm.id} value={rm.id}>{rm.name} - {rm.sku}</option>
 ))}
 </select>
 </div>
 <div className="form-group">
 <label>Qty per Unit *</label>
 <input 
 type="number" 
 step="0.01" 
 value={line.qty_per_unit} 
 onChange={(e) => updateBomLine(line.id, 'qty_per_unit', e.target.value)}
 placeholder="0.00"
 required
 />
 </div>
 <div className="form-group">
 <label>Unit *</label>
 <select 
 value={line.unit} 
 onChange={(e) => updateBomLine(line.id, 'unit', e.target.value)}
 required
 >
 <option value="kg">kg</option>
 <option value="g">g</option>
 <option value="L">L</option>
 <option value="mL">mL</option>
 <option value="m">m</option>
 <option value="cm">cm</option>
 <option value="pcs">pcs</option>
 <option value="box">box</option>
 <option value="roll">roll</option>
 <option value="pack">pack</option>
 </select>
 </div>
 <div className="form-group" style={{flex: '0'}}>
 <label>&nbsp;</label>
 <button 
 type="button" 
 className="btn btn-danger" 
 onClick={() => removeBomLine(line.id)}
 disabled={bomLines.length === 1}
 >
 
 </button>
 </div>
 </div>
 </div>
 ))}
 <button 
 type="button" 
 className="btn btn-secondary" 
 onClick={addBomLine}
 style={{marginTop: '10px'}}
 >
 + Add Raw Material Line
 </button>
 </div>
 
 <div className="modal-actions">
 <button type="button" className="btn btn-secondary" onClick={() => { setShowForm(''); setEditingBomProductId(null); }}>Cancel</button>
 <button type="submit" className="btn btn-primary" disabled={loading}>
 {loading ? 'Saving...' : (editingBomProductId ? 'Update BOM' : 'Save BOM')}
 </button>
 </div>
 </form>
 )}

 {showForm === 'productionOrder' && (
 <form onSubmit={submitProductionOrder}>
 <div className="form-row">
 <div className="form-group"><label>Product *</label><select value={forms.productionOrder.product_id} onChange={(e)=>handleFormChange('productionOrder','product_id',e.target.value)} required><option value="">Select product</option>{(data.products||[]).map(p=>(<option key={p.id} value={p.id}>{p.name}</option>))}</select></div>
 <div className="form-group"><label>Quantity Planned *</label><input type="number" value={forms.productionOrder.quantity_planned} onChange={(e)=>handleFormChange('productionOrder','quantity_planned',e.target.value)} required/></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Start Date</label><input type="date" value={forms.productionOrder.scheduled_start_date} onChange={(e)=>handleFormChange('productionOrder','scheduled_start_date',e.target.value)}/></div>
 <div className="form-group"><label>End Date</label><input type="date" value={forms.productionOrder.scheduled_end_date} onChange={(e)=>handleFormChange('productionOrder','scheduled_end_date',e.target.value)}/></div>
 <div className="form-group"><label>Priority</label><input type="number" value={forms.productionOrder.priority} onChange={(e)=>handleFormChange('productionOrder','priority',e.target.value)}/></div>
 </div>
 <div className="form-group"><label>Notes</label><textarea rows="2" value={forms.productionOrder.notes} onChange={(e)=>handleFormChange('productionOrder','notes',e.target.value)}/></div>
 <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Saving...':'Create Production Order'}</button></div>
 </form>
 )}

 {/* Machine/Equipment Add/Edit Form */}
 {showForm === 'machine' && (
 <form onSubmit={saveMachine}>
 <div className="form-row">
 <div className="form-group"><label>Equipment Name *</label><input value={machineForm.name} onChange={(e) => setMachineForm(p => ({...p, name: e.target.value}))} required placeholder="e.g., Packaging Machine A"/></div>
 <div className="form-group"><label>Equipment Type</label>
 <select value={machineForm.equipment_type} onChange={(e) => setMachineForm(p => ({...p, equipment_type: e.target.value}))}>
 <option value="">Select type</option>
 <option value="Manufacturing">Manufacturing</option>
 <option value="Packaging">Packaging</option>
 <option value="Mixing">Mixing</option>
 <option value="Filling">Filling</option>
 <option value="Labeling">Labeling</option>
 <option value="Testing">Testing/QC</option>
 <option value="Storage">Storage</option>
 <option value="Transport">Transport</option>
 <option value="Other">Other</option>
 </select>
 </div>
 <div className="form-group"><label>Serial Number</label><input value={machineForm.serial_number} onChange={(e) => setMachineForm(p => ({...p, serial_number: e.target.value}))} placeholder="Enter serial number"/></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Model</label><input value={machineForm.model} onChange={(e) => setMachineForm(p => ({...p, model: e.target.value}))}/></div>
 <div className="form-group"><label>Manufacturer</label><input value={machineForm.manufacturer} onChange={(e) => setMachineForm(p => ({...p, manufacturer: e.target.value}))}/></div>
 <div className="form-group"><label>Location</label><input value={machineForm.location} onChange={(e) => setMachineForm(p => ({...p, location: e.target.value}))} placeholder="e.g., Factory Floor A"/></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Purchase Date</label><input type="date" value={machineForm.purchase_date} onChange={(e) => setMachineForm(p => ({...p, purchase_date: e.target.value}))}/></div>
 <div className="form-group"><label>Purchase Cost (NGN)</label><input type="number" step="0.01" value={machineForm.purchase_cost} onChange={(e) => setMachineForm(p => ({...p, purchase_cost: e.target.value}))} placeholder="0.00"/></div>
 <div className="form-group"><label>Current Value (NGN)</label><input type="number" step="0.01" value={machineForm.current_value} onChange={(e) => setMachineForm(p => ({...p, current_value: e.target.value}))} placeholder="0.00"/></div>
 </div>
 <div className="form-row">
 <div className="form-group"><label>Depreciation Rate (%)</label><input type="number" step="0.1" value={machineForm.depreciation_rate} onChange={(e) => setMachineForm(p => ({...p, depreciation_rate: e.target.value}))}/></div>
 <div className="form-group"><label>Depreciation Method</label>
 <select value={machineForm.depreciation_method} onChange={(e) => setMachineForm(p => ({...p, depreciation_method: e.target.value}))}>
 <option value="straight_line">Straight Line</option>
 <option value="declining_balance">Declining Balance</option>
 </select>
 </div>
 <div className="form-group"><label>Status</label>
 <select value={machineForm.status} onChange={(e) => setMachineForm(p => ({...p, status: e.target.value}))}>
 <option value="Operational">Operational</option>
 <option value="Under Maintenance">Under Maintenance</option>
 <option value="Out of Service">Out of Service</option>
 <option value="Decommissioned">Decommissioned</option>
 </select>
 </div>
 </div>
 <div className="form-group"><label>Notes</label><textarea rows="2" value={machineForm.notes} onChange={(e) => setMachineForm(p => ({...p, notes: e.target.value}))}/></div>
 <div className="modal-actions">
 <button type="button" className="btn btn-secondary" onClick={() => setShowForm('')}>Cancel</button>
 <button type="submit" className="btn btn-primary" disabled={loading}>{loading ? 'Saving...' : (editingMachine ? 'Update' : 'Add')} Machine</button>
 </div>
 </form>
 )}
 </div>
 </div>
 </div>
 )}

 {loading && (<div className="loading-overlay"><div className="loading-spinner"> Loading...</div></div>)}
 </div>
 </div>
 );

 // Attendance PIN-based clock in/out
 async function handlePinClock(action) {
 const pinInput = document.getElementById('clockPinInput');
 const pin = pinInput.value.trim();
 
 if (!pin || pin.length !== 4) {
 notify('Please enter a valid 4-digit PIN', 'error');
 return;
 }
 
 try {
 setLoading(true);
 let loc;
 try {
 loc = await requireLocation();
 } catch (geoErr) {
 notify(geoErr.message || 'Location permission is required to clock in/out.', 'error');
 setLoading(false);
 return;
 }
 const res = await authedFetch('/api/attendance/quick-attendance', {
 method: 'POST',
 headers: { 'Content-Type': 'application/json' },
 body: JSON.stringify({ pin, action, notes: '', ...loc })
 });
 
 const result = await res.json();
 
 if (result.success) {
 notify(`${result.message} - ${result.staff_name}`, 'success');
 pinInput.value = '';
 fetchData('attendance');
 // Personalized greeting on clock-in (lateness policy)
 if (action === 'clock_in' && result.staff_id) {
 try {
 const gr = await authedFetch('/api/announcements/clock-in-greeting', {
 method:'POST',
 headers:{'Content-Type':'application/json'},
 body: JSON.stringify({ staff_id: result.staff_id, clock_in_time: result.clock_in_time || new Date().toISOString() })
 });
 if (gr.ok) {
 const g = await gr.json();
 setClockInGreeting(g);
 // Speak the greeting through the single Company Radio channel so it can
 // never overlap with an announcement or another radio clip. Prepend it so
 // the person clocking in hears it promptly.
 if (g.message) {
 const greetEvent = {
 id: `greeting:${result.staff_id}:${Date.now()}`,
 type: 'greeting',
 title: g.staff_name || 'Welcome',
 message: g.message,
 audio_url: null,
 priority: 9,
 };
 setRadioQueue(q => [greetEvent, ...q]);
 }
 setTimeout(() => setClockInGreeting(null), 12000);
 }
 } catch(e) { /* non-blocking */ }
 }
 } else {
 notify(result.message, 'error');
 }
 } catch (e) {
 notify(`Error: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }
 
 // View current attendance status
 async function viewAttendanceStatus() {
 try {
 setLoading(true);
 // Derive status from attendance records
 const attendanceRes = await authedFetch('/api/attendance/');
 if (!attendanceRes.ok) throw new Error('Failed to fetch attendance');
 const attendanceData = await attendanceRes.json();
 
 // Extract items array from response
 const allAttendance = attendanceData.items || attendanceData || [];
 
 // Get today's attendance records - use clock_in field
 const today = new Date().toISOString().split('T')[0];
 const todayAttendance = allAttendance.filter(a => a.clock_in?.startsWith(today));
 
 // Derive status from records
 const statusList = (data.staff || []).map(staff => {
 const todayRecord = todayAttendance.find(a => a.staff_id === staff.id);
 let status = 'not_clocked_in';
 if (todayRecord) {
 status = todayRecord.clock_out ? 'clocked_out' : 'clocked_in';
 }
 
 // Calculate hours
 let hoursWorked = 0;
 let hoursSoFar = 0;
 if (todayRecord) {
 const clockIn = new Date(todayRecord.clock_in);
 const clockOut = todayRecord.clock_out ? new Date(todayRecord.clock_out) : new Date();
 const diffMs = clockOut - clockIn;
 const hours = diffMs / (1000 * 60 * 60);
 if (todayRecord.clock_out) {
 hoursWorked = hours;
 } else {
 hoursSoFar = hours;
 }
 }
 
 return {
 staff_id: staff.id,
 employee_id: staff.employee_id,
 staff_name: `${staff.first_name} ${staff.last_name}`,
 position: staff.position,
 status: status,
 clock_in: todayRecord?.clock_in,
 clock_out: todayRecord?.clock_out,
 hours_worked: hoursWorked,
 hours_so_far: hoursSoFar
 };
 });
 
 displayStatusList(statusList);
 
 } catch (err) {
 console.error('Error fetching status:', err);
 alert('Failed to fetch attendance status: ' + err.message);
 } finally {
 setLoading(false);
 }
 }

 function displayStatusList(statusList) {
 
 const displayArea = document.getElementById('attendanceDisplayArea');
 if (!displayArea) return;
 
 const clockedIn = statusList.filter(s => s.status === 'clocked_in');
 const clockedOut = statusList.filter(s => s.status === 'clocked_out');
 const notClockedIn = statusList.filter(s => s.status === 'not_clocked_in');
 
 displayArea.innerHTML = `
 <div class="attendance-status-container">
 <h3>Current Attendance Status</h3>
 
 <div class="status-summary">
 <div class="status-badge clocked-in"> Clocked In: ${clockedIn.length}</div>
 <div class="status-badge clocked-out"> Clocked Out: ${clockedOut.length}</div>
 <div class="status-badge not-clocked"> Not Clocked In: ${notClockedIn.length}</div>
 </div>
 
 <div class="status-section">
 <h4> Currently Clocked In</h4>
 ${clockedIn.length > 0 ? `
 <table class="data-table">
 <thead><tr><th>Employee ID</th><th>Name</th><th>Position</th><th>Clock In Time</th><th>Hours So Far</th></tr></thead>
 <tbody>
 ${clockedIn.map(s => `
 <tr>
 <td>${s.employee_id}</td>
 <td>${s.staff_name}</td>
 <td>${s.position || 'N/A'}</td>
 <td>${new Date(s.clock_in).toLocaleString()}</td>
 <td>${s.hours_so_far.toFixed(2)} hrs</td>
 </tr>
 `).join('')}
 </tbody>
 </table>
 ` : '<p class="no-data">No staff currently clocked in</p>'}
 </div>
 
 <div class="status-section">
 <h4> Clocked Out Today</h4>
 ${clockedOut.length > 0 ? `
 <table class="data-table">
 <thead><tr><th>Employee ID</th><th>Name</th><th>Position</th><th>Clock In</th><th>Clock Out</th><th>Hours Worked</th></tr></thead>
 <tbody>
 ${clockedOut.map(s => `
 <tr>
 <td>${s.employee_id}</td>
 <td>${s.staff_name}</td>
 <td>${s.position || 'N/A'}</td>
 <td>${new Date(s.clock_in).toLocaleTimeString()}</td>
 <td>${new Date(s.clock_out).toLocaleTimeString()}</td>
 <td>${(s.hours_worked || 0).toFixed(2)} hrs</td>
 </tr>
 `).join('')}
 </tbody>
 </table>
 ` : '<p class="no-data">No staff have clocked out today</p>'}
 </div>
 </div>
 `;
 }
 
 // View detailed attendance log with punctuality
 async function viewDetailedLog() {
 try {
 setLoading(true);
 console.log('Fetching detailed log...');
 const res = await authedFetch('/api/attendance/detailed-log');
 console.log('Response status:', res.status);
 if (!res.ok) {
 const errorText = await res.text();
 console.error('Error response:', errorText);
 throw new Error(`Failed to fetch detailed log: ${res.status} ${errorText}`);
 }
 
 const logData = await res.json();
 console.log('Received log data:', logData);
 
 const displayArea = document.getElementById('attendanceDisplayArea');
 if (!displayArea) {
 console.error('Display area not found!');
 return;
 }
 
 const getPunctualityBadge = (status, minutes) => {
 if (status === 'early') return `<span class="punctuality-badge early"> Early (${minutes} min)</span>`;
 if (status === 'on_time') return `<span class="punctuality-badge on-time"> On Time (${minutes} min)</span>`;
 if (status === 'slightly_late') return `<span class="punctuality-badge slightly-late"> Slightly Late (${minutes} min)</span>`;
 return `<span class="punctuality-badge late"> Late (${minutes} min)</span>`;
 };
 
 displayArea.innerHTML = `
 <div class="detailed-log-container">
 <h3>Detailed Attendance Log (Last 30 Days)</h3>
 <p class="log-info">Showing attendance records with punctuality analysis. Standard start time: 9:00 AM</p>
 
 ${logData.length > 0 ? `
 <div class="table-container">
 <table class="data-table">
 <thead>
 <tr>
 <th>Date</th>
 <th>Employee ID</th>
 <th>Name</th>
 <th>Position</th>
 <th>Clock In</th>
 <th>Clock Out</th>
 <th>Hours</th>
 <th>Punctuality</th>
 </tr>
 </thead>
 <tbody>
 ${logData.map(log => `
 <tr>
 <td>${log.date}</td>
 <td>${log.employee_id}</td>
 <td>${log.staff_name}</td>
 <td>${log.position || 'N/A'}</td>
 <td>${log.clock_in}</td>
 <td>${log.clock_out || 'In Progress'}</td>
 <td>${parseFloat(log.hours_worked || 0).toFixed(2)} hrs</td>
 <td>${getPunctualityBadge(log.punctuality_status, log.punctuality_minutes)}</td>
 </tr>
 `).join('')}
 </tbody>
 </table>
 </div>
 ` : '<p class="no-data">No attendance records found for the last 30 days</p>'}
 </div>
 `;
 } catch (e) {
 console.error('Error in viewDetailedLog:', e);
 notify(`Error fetching detailed log: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }
 
 // View best performing staff
 async function viewBestPerformers() {
 try {
 setLoading(true);
 const res = await authedFetch('/api/attendance/best-performers?limit=10');
 if (!res.ok) throw new Error('Failed to fetch best performers');
 
 const performers = await res.json();
 
 const displayArea = document.getElementById('attendanceDisplayArea');
 if (!displayArea) return;
 
 displayArea.innerHTML = `
 <div class="best-performers-container">
 <h3>Best Performing Staff (Last 30 Days)</h3>
 <p class="performers-info">Ranking based on punctuality (70%) and attendance regularity (30%)</p>
 
 ${performers.length > 0 ? `
 <div class="performers-grid">
 ${performers.map((staff, index) => `
 <div class="performer-card ${index < 3 ? 'top-performer' : ''}">
 <div class="performer-rank">${index < 3 ? ['#1', '#2', '#3'][index] : `#${index + 1}`}</div>
 <div class="performer-info">
 <h4>${staff.staff_name}</h4>
 <p class="performer-id">${staff.employee_id} ${staff.position || 'N/A'}</p>
 <div class="performer-score">Performance Score: <strong>${staff.performance_score}%</strong></div>
 </div>
 <div class="performer-stats">
 <div class="stat-row">
 <span class="stat-label">Days Attended:</span>
 <span class="stat-value">${staff.total_days_attended}</span>
 </div>
 <div class="stat-row">
 <span class="stat-label"> Total Hours:</span>
 <span class="stat-value">${staff.total_hours_worked} hrs</span>
 </div>
 <div class="stat-row">
 <span class="stat-label">Avg Hours/Day:</span>
 <span class="stat-value">${staff.avg_hours_per_day} hrs</span>
 </div>
 <div class="stat-row">
 <span class="stat-label"> Early Arrivals:</span>
 <span class="stat-value">${staff.early_arrivals}</span>
 </div>
 <div class="stat-row">
 <span class="stat-label"> On Time:</span>
 <span class="stat-value">${staff.on_time_arrivals}</span>
 </div>
 <div class="stat-row">
 <span class="stat-label"> Late Arrivals:</span>
 <span class="stat-value">${staff.late_arrivals}</span>
 </div>
 <div class="stat-row">
 <span class="stat-label"> Punctuality:</span>
 <span class="stat-value">${staff.punctuality_score}%</span>
 </div>
 </div>
 </div>
 `).join('')}
 </div>
 ` : '<p class="no-data">No performance data available for the last 30 days</p>'}
 </div>
 `;
 } catch (e) {
 notify(`Error fetching best performers: ${e.message}`, 'error');
 } finally {
 setLoading(false);
 }
 }
}

export default AppMain;




