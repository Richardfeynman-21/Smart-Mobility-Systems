"use strict";

let vehicles = [];
let users = [];
let reservations = [];
let maintenanceRecords = [];
let notificationItems = [];
let messageTimer = null;

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[char]));
}

function localDateTimeValue(date) {
    const adjusted = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return adjusted.toISOString().slice(0, 16);
}

function setDefaultBookingTimes() {
    const now = new Date();
    now.setSeconds(0, 0);
    const end = new Date(now.getTime() + 60 * 60 * 1000);
    if (!$('bookingStart').value) $('bookingStart').value = localDateTimeValue(now);
    if (!$('bookingEnd').value) $('bookingEnd').value = localDateTimeValue(end);
}

function showMessage(message, type = "success") {
    const box = $('messageBox');
    box.textContent = message;
    box.className = `message-box ${type}`;
    if (messageTimer) clearTimeout(messageTimer);
    messageTimer = setTimeout(() => { box.className = 'message-box'; box.textContent = ''; }, 5000);
}

async function api(url, options = {}) {
    const response = await fetch(url, {
        ...options,
        headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) }
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.message || `Request failed (${response.status}).`);
    return data;
}

async function loadDashboard() {
    const selectedUser = $('studentSelect').value;
    try {
        const [vehicleData, userData, reservationData, alertData, maintenanceData, analyticsData] = await Promise.all([
            api('/vehicles'), api('/users'), api('/reservations'), api('/vehicles/alerts'), api('/maintenance'), api('/analytics')
        ]);
        vehicles = vehicleData;
        users = userData;
        reservations = reservationData;
        maintenanceRecords = maintenanceData;
        populateUsers(selectedUser);
        populateMaintenanceVehicles();
        renderVehicles();
        renderReservations();
        renderAlerts(alertData);
        renderMaintenance();
        renderAnalytics(analyticsData);
        await loadNotifications();
    } catch (error) {
        console.error('Dashboard loading error:', error);
        showMessage(`Unable to load dashboard: ${error.message}`, 'error');
    }
}

function populateUsers(previousValue = '') {
    const select = $('studentSelect');
    select.innerHTML = '<option value="">Select user</option>';
    users.forEach((user) => {
        const option = document.createElement('option');
        option.value = user.user_id;
        option.textContent = `${user.user_id} - ${user.name}`;
        select.appendChild(option);
    });
    if (previousValue && users.some((user) => user.user_id === previousValue)) select.value = previousValue;
}

function populateMaintenanceVehicles() {
    const select = $('maintenanceVehicle');
    const previous = select.value;
    select.innerHTML = '<option value="">Select vehicle</option>';
    vehicles.forEach((vehicle) => {
        const option = document.createElement('option');
        option.value = vehicle.vehicle_id;
        option.textContent = `${vehicle.vehicle_id} — ${vehicle.vehicle_type.replaceAll('_', ' ')}`;
        select.appendChild(option);
    });
    if (vehicles.some((vehicle) => vehicle.vehicle_id === previous)) select.value = previous;
}

function renderVehicles() {
    const selectedUser = $('studentSelect').value;
    $('totalVehicles').textContent = vehicles.length;
    $('availableVehicles').textContent = vehicles.filter(v => v.status === 'AVAILABLE').length;
    $('reservedVehicles').textContent = vehicles.filter(v => v.status === 'RESERVED').length;
    $('alertVehicles').textContent = vehicles.filter(v => Number(v.battery_level) <= 20 || v.status === 'MAINTENANCE').length;
    const body = $('vehicleTableBody');
    if (!vehicles.length) { body.innerHTML = '<tr><td colspan="7" class="loading">No vehicles found.</td></tr>'; return; }
    body.innerHTML = vehicles.map((v) => {
        const currentReservation = reservations.find(r => r.vehicle_id === v.vehicle_id && r.user_id === selectedUser && ['ACTIVE', 'CONFIRMED'].includes(r.status));
        let action = '<button class="action-button disabled-button" disabled>Select a user</button>';
        if (selectedUser && v.status !== 'MAINTENANCE') {
            if (currentReservation && currentReservation.status !== 'WAITLISTED') {
                const startsAt = currentReservation.booking_start ? new Date(String(currentReservation.booking_start).replace(' ', 'T') + (/[zZ]|[+-]\d{2}:?\d{2}$/.test(String(currentReservation.booking_start)) ? '' : 'Z')) : null;
                if (currentReservation.status === 'ACTIVE' || (startsAt && startsAt <= new Date())) {
                    action = `<button class="action-button return-button" data-action="return" data-id="${currentReservation.reservation_id}">Return</button>`;
                } else {
                    action = `<button class="action-button secondary-button" data-action="cancel" data-id="${currentReservation.reservation_id}">Cancel Booking</button>`;
                }
            } else {
                action = `<button class="action-button reserve-button" data-action="reserve" data-vehicle="${escapeHtml(v.vehicle_id)}">Book / Join Waitlist</button>`;
            }
        } else if (v.status === 'MAINTENANCE') {
            action = '<button class="action-button disabled-button" disabled>Maintenance</button>';
        }
        return `<tr>
            <td><strong>${escapeHtml(v.vehicle_id)}</strong></td>
            <td>${escapeHtml(String(v.vehicle_type).replaceAll('_', ' '))}</td>
            <td>${escapeHtml(v.location)}</td>
            <td class="${Number(v.battery_level) <= 20 ? 'battery-low' : 'battery-normal'}">${escapeHtml(v.battery_level)}%</td>
            <td><span class="status-badge status-${String(v.status).toLowerCase().replaceAll('_', '-')}">${escapeHtml(v.status)}</span></td>
            <td>${escapeHtml(v.user_id || '—')}</td><td>${action}</td></tr>`;
    }).join('');
}

function formatDate(value) {
    if (!value) return '—';
    let raw = String(value);
    if (!/[zZ]|[+-]\d{2}:?\d{2}$/.test(raw)) raw = raw.replace(' ', 'T') + 'Z';
    const date = new Date(raw);
    return Number.isNaN(date.getTime()) ? escapeHtml(value) : date.toLocaleString();
}

function renderReservations() {
    const currentUser = $('studentSelect').value;
    const visible = reservations.filter(r => !currentUser || r.user_id === currentUser);
    const body = $('reservationTableBody');
    if (!visible.length) { body.innerHTML = '<tr><td colspan="7" class="loading">No reservations for this user.</td></tr>'; return; }
    body.innerHTML = visible.map(r => {
        let action = '—';
        if (['ACTIVE', 'CONFIRMED', 'WAITLISTED'].includes(r.status)) {
            if (r.status === 'ACTIVE' || r.status === 'CONFIRMED') {
                action = `<button class="action-button secondary-button" data-action="cancel" data-id="${r.reservation_id}">Cancel</button>`;
                const start = r.booking_start ? new Date(String(r.booking_start).replace(' ', 'T') + (/[zZ]|[+-]\d{2}:?\d{2}$/.test(String(r.booking_start)) ? '' : 'Z')) : null;
                if (r.status === 'ACTIVE' || (start && start <= new Date())) {
                    action += ` <button class="action-button return-button" data-action="return" data-id="${r.reservation_id}">Return</button>`;
                }
            } else {
                action = `<button class="action-button secondary-button" data-action="cancel" data-id="${r.reservation_id}">Leave queue</button>`;
            }
        }
        return `<tr><td>${r.reservation_id}</td><td>${escapeHtml(r.vehicle_id)}</td><td>${escapeHtml(r.user_id)}</td><td>${formatDate(r.booking_start)}</td><td>${formatDate(r.booking_end)}</td><td><span class="status-badge status-${String(r.status).toLowerCase().replaceAll('_', '-')}">${escapeHtml(r.status)}</span></td><td>${action}</td></tr>`;
    }).join('');
}

function renderAlerts(data) {
    const container = $('alertsContainer');
    if (!data.vehicles?.length) { container.innerHTML = '<p class="empty-state">No active vehicle alerts.</p>'; return; }
    container.innerHTML = data.vehicles.map(v => {
        const reasons = [];
        if (Number(v.battery_level) <= 20) reasons.push(`Low battery (${v.battery_level}%)`);
        if (v.status === 'MAINTENANCE') reasons.push('Under maintenance');
        return `<article class="alert-card"><h4>${escapeHtml(v.vehicle_id)}</h4><p>${reasons.map(escapeHtml).join(' · ')}</p><p>Location: ${escapeHtml(v.location)}</p></article>`;
    }).join('');
}

function renderMaintenance() {
    const container = $('maintenanceList');
    if (!maintenanceRecords.length) { container.innerHTML = '<p class="empty-state">No maintenance records yet.</p>'; return; }
    container.innerHTML = maintenanceRecords.map(m => `<article class="list-item"><div><strong>${escapeHtml(m.vehicle_id)}</strong><p>${escapeHtml(m.description)}</p><small>${escapeHtml(m.status)} · ${formatDate(m.scheduled_start)}${m.scheduled_end ? ` – ${formatDate(m.scheduled_end)}` : ''}</small></div>${m.status === 'SCHEDULED' ? `<button class="action-button return-button" data-action="maintenance-complete" data-id="${m.maintenance_id}">Mark complete</button>` : ''}</article>`).join('');
}

function renderAnalytics(data) {
    $('analyticsReservations').textContent = data.reservation_total ?? 0;
    $('analyticsWaiting').textContent = data.waitlist_total ?? 0;
    $('analyticsUtilization').textContent = `${data.current_utilization_percent ?? 0}%`;
    const statuses = data.reservations_by_status || [];
    const maxStatus = Math.max(1, ...statuses.map(item => Number(item.count) || 0));
    $('statusAnalytics').innerHTML = statuses.length ? statuses.map(item => {
        const width = Math.max(3, (Number(item.count) / maxStatus) * 100);
        return `<div class="bar-row"><div class="metric-row"><span>${escapeHtml(item.status)}</span><strong>${item.count}</strong></div><div class="bar-track"><div class="bar-fill" style="width:${width}%"></div></div></div>`;
    }).join('') : '<p class="empty-state">No reservation data yet.</p>';
    const popular = data.popular_vehicles || [];
    const maxPopular = Math.max(1, ...popular.map(item => Number(item.booking_count) || 0));
    $('popularVehicles').innerHTML = popular.length ? popular.map(item => {
        const width = Math.max(3, (Number(item.booking_count) / maxPopular) * 100);
        return `<div class="bar-row"><div class="metric-row"><span>${escapeHtml(item.vehicle_id)}</span><strong>${item.booking_count} bookings</strong></div><div class="bar-track"><div class="bar-fill" style="width:${width}%"></div></div></div>`;
    }).join('') : '<p class="empty-state">No booking data yet.</p>';
}

async function loadNotifications() {
    const userId = $('studentSelect').value;
    const container = $('notificationsList');
    if (!userId) { container.innerHTML = '<p class="empty-state">Select a user to view notifications.</p>'; return; }
    try {
        notificationItems = await api(`/notifications?user_id=${encodeURIComponent(userId)}`);
        container.innerHTML = notificationItems.length ? notificationItems.map(n => `<article class="list-item ${Number(n.is_read) ? '' : 'unread'}"><div><p>${escapeHtml(n.message)}</p><small>${escapeHtml(n.notification_type)} · ${formatDate(n.created_at)}</small></div>${Number(n.is_read) ? '' : `<button class="action-button secondary-button" data-action="notification-read" data-id="${n.notification_id}">Mark read</button>`}</article>`).join('') : '<p class="empty-state">No notifications yet.</p>';
    } catch (error) {
        console.error('Notifications error:', error);
        container.innerHTML = `<p class="empty-state">Unable to load notifications: ${escapeHtml(error.message)}</p>`;
    }
}

async function handleAction(event) {
    const button = event.target.closest('button[data-action]');
    if (!button) return;
    const action = button.dataset.action;
    const id = button.dataset.id;
    try {
        if (action === 'reserve') {
            const userId = $('studentSelect').value;
            if (!userId) throw new Error('Select a user first.');
            const start = $('bookingStart').value;
            const end = $('bookingEnd').value;
            if (!start || !end || new Date(end) <= new Date(start)) throw new Error('Choose a valid start and end time.');
            const result = await api('/reservations', { method: 'POST', body: JSON.stringify({ vehicle_id: button.dataset.vehicle, user_id: userId, booking_start: new Date(start).toISOString(), booking_end: new Date(end).toISOString() }) });
            showMessage(result.message, result.status === 'WAITLISTED' ? 'error' : 'success');
        } else if (action === 'cancel') {
            const result = await api(`/reservations/${id}/cancel`, { method: 'PUT' });
            showMessage(result.message);
        } else if (action === 'return') {
            const result = await api(`/reservations/${id}/return`, { method: 'PUT' });
            showMessage(result.message);
        } else if (action === 'maintenance-complete') {
            const result = await api(`/maintenance/${id}/complete`, { method: 'PUT' });
            showMessage(result.message);
        } else if (action === 'notification-read') {
            await api(`/notifications/${id}/read`, { method: 'PUT', body: JSON.stringify({ user_id: $('studentSelect').value }) });
            await loadNotifications();
            return;
        }
        await loadDashboard();
    } catch (error) {
        console.error(`${action} action failed:`, error);
        showMessage(error.message, 'error');
    }
}

async function submitMaintenance(event) {
    event.preventDefault();
    try {
        const payload = {
            vehicle_id: $('maintenanceVehicle').value,
            description: $('maintenanceDescription').value.trim(),
            scheduled_start: $('maintenanceStart').value ? new Date($('maintenanceStart').value).toISOString() : null,
            scheduled_end: $('maintenanceEnd').value ? new Date($('maintenanceEnd').value).toISOString() : null
        };
        const result = await api('/maintenance', { method: 'POST', body: JSON.stringify(payload) });
        $('maintenanceForm').reset();
        showMessage(result.message);
        await loadDashboard();
    } catch (error) {
        showMessage(error.message, 'error');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    setDefaultBookingTimes();
    $('refreshButton').addEventListener('click', loadDashboard);
    $('refreshNotifications').addEventListener('click', loadNotifications);
    $('studentSelect').addEventListener('change', () => { renderVehicles(); renderReservations(); loadNotifications(); });
    $('vehicleTableBody').addEventListener('click', handleAction);
    $('reservationTableBody').addEventListener('click', handleAction);
    $('maintenanceList').addEventListener('click', handleAction);
    $('notificationsList').addEventListener('click', handleAction);
    $('maintenanceForm').addEventListener('submit', submitMaintenance);
    loadDashboard();
    window.setInterval(loadDashboard, 15000);
});
