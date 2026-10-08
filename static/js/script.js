let vehicles = [];
let students = [];
let reservations = [];


// =====================================================
// INITIALIZATION
// =====================================================

document.addEventListener("DOMContentLoaded", () => {

    document
        .getElementById("refreshButton")
        .addEventListener("click", () => {
            loadDashboard();
        });


    document
        .getElementById("studentSelect")
        .addEventListener("change", () => {
            displayVehicles();
        });


    // One click handler for ALL vehicle actions
    document
        .getElementById("vehicleTableBody")
        .addEventListener("click", handleVehicleAction);


    loadDashboard();

});


// =====================================================
// LOAD DASHBOARD
// =====================================================

async function loadDashboard() {

    try {

        showTableLoading();


        const responses = await Promise.all([
            fetch("/vehicles"),
            fetch("/users"),
            fetch("/reservations"),
            fetch("/vehicles/alerts")
        ]);


        for (const response of responses) {

            if (!response.ok) {

                throw new Error(
                    `Server returned ${response.status}`
                );

            }

        }


        vehicles =
            await responses[0].json();

        students =
            await responses[1].json();

        reservations =
            await responses[2].json();

        const alerts =
            await responses[3].json();


        populateStudents();

        updateSummary();

        displayVehicles();

        displayAlerts(alerts);


    } catch (error) {

        console.error(
            "Dashboard loading error:",
            error
        );


        showMessage(
            "Unable to load dashboard data.",
            "error"
        );

    }

}


// =====================================================
// STUDENT DROPDOWN
// =====================================================

function populateStudents() {

    const select =
        document.getElementById(
            "studentSelect"
        );


    const previousValue =
        select.value;


    select.innerHTML = `
        <option value="">
            Select Student
        </option>
    `;


    students.forEach(student => {

        const option =
            document.createElement("option");


        option.value =
            student.user_id;


        option.textContent =
            `${student.user_id} - ${student.name}`;


        select.appendChild(option);

    });


    if (
        previousValue &&
        students.some(
            student =>
                student.user_id === previousValue
        )
    ) {

        select.value =
            previousValue;

    }

}


// =====================================================
// SUMMARY
// =====================================================

function updateSummary() {

    const total =
        vehicles.length;


    const available =
        vehicles.filter(
            vehicle =>
                vehicle.status === "AVAILABLE"
        ).length;


    const reserved =
        vehicles.filter(
            vehicle =>
                vehicle.status === "RESERVED"
        ).length;


    const alerts =
        vehicles.filter(
            vehicle =>
                vehicle.battery_level <= 20 ||
                vehicle.status === "MAINTENANCE"
        ).length;


    document.getElementById(
        "totalVehicles"
    ).textContent = total;


    document.getElementById(
        "availableVehicles"
    ).textContent = available;


    document.getElementById(
        "reservedVehicles"
    ).textContent = reserved;


    document.getElementById(
        "alertVehicles"
    ).textContent = alerts;

}


// =====================================================
// DISPLAY VEHICLES
// =====================================================

function displayVehicles() {

    const tableBody =
        document.getElementById(
            "vehicleTableBody"
        );


    const selectedStudent =
        document.getElementById(
            "studentSelect"
        ).value;


    tableBody.innerHTML = "";


    if (vehicles.length === 0) {

        tableBody.innerHTML = `
            <tr>
                <td colspan="7" class="loading">
                    No vehicles found.
                </td>
            </tr>
        `;

        return;

    }


    vehicles.forEach(vehicle => {

        const row =
            document.createElement("tr");


        const batteryClass =
            vehicle.battery_level <= 20
                ? "battery-low"
                : "battery-normal";


        const statusClass =
            getStatusClass(
                vehicle.status
            );


        row.innerHTML = `

            <td>
                <strong>
                    ${escapeHtml(vehicle.vehicle_id)}
                </strong>
            </td>

            <td>
                ${formatVehicleType(
                    vehicle.vehicle_type
                )}
            </td>

            <td>
                ${escapeHtml(vehicle.location)}
            </td>

            <td class="${batteryClass}">
                ${vehicle.battery_level}%
            </td>

            <td>

                <span class="status-badge ${statusClass}">
                    ${escapeHtml(vehicle.status)}
                </span>

            </td>

            <td>
                ${vehicle.user_id
                    ? escapeHtml(vehicle.user_id)
                    : "—"}
            </td>

            <td>
                ${createActionButton(
                    vehicle,
                    selectedStudent
                )}
            </td>

        `;


        tableBody.appendChild(row);

    });

}


// =====================================================
// CREATE ACTION BUTTON
// =====================================================

function createActionButton(
    vehicle,
    selectedStudent
) {

    if (!selectedStudent) {

        return `
            <button
                type="button"
                class="action-button disabled-button"
                disabled>

                Select Student

            </button>
        `;

    }


    if (
        vehicle.status === "AVAILABLE"
    ) {

        return `
            <button
                type="button"
                class="action-button reserve-button"
                data-action="reserve"
                data-vehicle-id="${escapeHtml(vehicle.vehicle_id)}">

                Reserve

            </button>
        `;

    }


    if (
        vehicle.status === "RESERVED" &&
        vehicle.user_id === selectedStudent
    ) {

        const reservation =
            reservations.find(
                reservation =>
                    reservation.vehicle_id ===
                        vehicle.vehicle_id &&

                    reservation.user_id ===
                        selectedStudent &&

                    reservation.status ===
                        "ACTIVE"
            );


        if (reservation) {

            return `
                <button
                    type="button"
                    class="action-button return-button"
                    data-action="return"
                    data-reservation-id="${reservation.reservation_id}">

                    Return

                </button>
            `;

        }

    }


    if (
        vehicle.status === "RESERVED"
    ) {

        return `
            <button
                type="button"
                class="action-button disabled-button"
                disabled>

                Reserved

        </button>
        `;

    }


    if (
        vehicle.status === "IN_USE"
    ) {

        return `
            <button
                type="button"
                class="action-button disabled-button"
                disabled>

                In Use

            </button>
        `;

    }


    if (
        vehicle.status === "MAINTENANCE"
    ) {

        return `
            <button
                type="button"
                class="action-button disabled-button"
                disabled>

                Maintenance

            </button>
        `;

    }


    return `
        <button
            type="button"
            class="action-button disabled-button"
            disabled>

            Unavailable

        </button>
    `;

}


// =====================================================
// HANDLE VEHICLE BUTTON CLICKS
// =====================================================

async function handleVehicleAction(event) {

    const button =
        event.target.closest(
            "button[data-action]"
        );


    if (!button) {
        return;
    }


    const action =
        button.dataset.action;


    if (action === "reserve") {

        const vehicleId =
            button.dataset.vehicleId;


        await reserveVehicle(
            vehicleId
        );

    }


    if (action === "return") {

        const reservationId =
            button.dataset.reservationId;


        await returnVehicle(
            reservationId
        );

    }

}


// =====================================================
// RESERVE VEHICLE
// =====================================================

async function reserveVehicle(
    vehicleId
) {

    const studentSelect =
        document.getElementById(
            "studentSelect"
        );


    const studentId =
        studentSelect.value;


    if (!studentId) {

        showMessage(
            "Please select a student first.",
            "error"
        );

        return;

    }


    try {

        const response =
            await fetch(
                "/reservations",
                {
                    method: "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body: JSON.stringify({

                        vehicle_id:
                            vehicleId,

                        user_id:
                            studentId

                    })

                }
            );


        const data =
            await response.json();


        if (!response.ok) {

            throw new Error(
                data.message ||
                "Reservation failed."
            );

        }


        showMessage(
            `Vehicle ${vehicleId} reserved successfully.`,
            "success"
        );


        await loadDashboard();


        studentSelect.value =
            studentId;


        displayVehicles();


    } catch (error) {

        console.error(
            "Reservation error:",
            error
        );


        showMessage(
            error.message,
            "error"
        );

    }

}


// =====================================================
// RETURN VEHICLE
// =====================================================

async function returnVehicle(
    reservationId
) {

    try {

        const response =
            await fetch(
                `/reservations/${reservationId}/return`,
                {
                    method: "PUT"
                }
            );


        const data =
            await response.json();


        if (!response.ok) {

            throw new Error(
                data.message ||
                "Vehicle return failed."
            );

        }


        showMessage(
            "Vehicle returned successfully.",
            "success"
        );


        await loadDashboard();


    } catch (error) {

        console.error(
            "Return error:",
            error
        );


        showMessage(
            error.message,
            "error"
        );

    }

}


// =====================================================
// ALERTS
// =====================================================

function displayAlerts(
    alertData
) {

    const container =
        document.getElementById(
            "alertsContainer"
        );


    container.innerHTML = "";


    if (
        !alertData.vehicles ||
        alertData.vehicles.length === 0
    ) {

        container.innerHTML = `
            <p class="loading">
                No active vehicle alerts.
            </p>
        `;

        return;

    }


    alertData.vehicles.forEach(vehicle => {

        const card =
            document.createElement("div");


        card.className =
            "alert-card";


        let reason = "";


        if (
            vehicle.battery_level <= 20
        ) {

            reason =
                `Low battery (${vehicle.battery_level}%)`;

        }


        if (
            vehicle.status === "MAINTENANCE"
        ) {

            if (reason) {
                reason += " • ";
            }

            reason +=
                "Maintenance required";

        }


        card.innerHTML = `

            <h4>
                ${escapeHtml(
                    vehicle.vehicle_id
                )}
            </h4>

            <p>
                ${reason}
            </p>

            <p>
                Location:
                ${escapeHtml(
                    vehicle.location
                )}
            </p>

        `;


        container.appendChild(card);

    });

}


// =====================================================
// STATUS
// =====================================================

function getStatusClass(
    status
) {

    switch (status) {

        case "AVAILABLE":
            return "status-available";

        case "RESERVED":
            return "status-reserved";

        case "IN_USE":
            return "status-in-use";

        case "MAINTENANCE":
            return "status-maintenance";

        default:
            return "";

    }

}


// =====================================================
// VEHICLE TYPE
// =====================================================

function formatVehicleType(
    type
) {

    return type
        .replaceAll("_", " ")
        .toLowerCase()
        .replace(
            /\b\w/g,
            char =>
                char.toUpperCase()
        );

}


// =====================================================
// TABLE LOADING
// =====================================================

function showTableLoading() {

    const tableBody =
        document.getElementById(
            "vehicleTableBody"
        );


    tableBody.innerHTML = `
        <tr>
            <td colspan="7" class="loading">
                Loading vehicles...
            </td>
        </tr>
    `;

}


// =====================================================
// MESSAGE
// =====================================================

function showMessage(
    message,
    type
) {

    const box =
        document.getElementById(
            "messageBox"
        );


    box.textContent =
        message;


    box.className =
        `message-box ${type}`;


    setTimeout(() => {

        box.className =
            "message-box";

        box.textContent =
            "";

    }, 4000);

}


// =====================================================
// HTML ESCAPING
// =====================================================

function escapeHtml(
    value
) {

    const div =
        document.createElement(
            "div"
        );


    div.textContent =
        value ?? "";


    return div.innerHTML;

}