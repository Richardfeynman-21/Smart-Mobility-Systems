-- Smart Campus Mobility bootstrap schema (non-destructive).
-- This file intentionally does NOT DROP or truncate existing tables/data.
CREATE DATABASE IF NOT EXISTS smart_campus_mobility
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE smart_campus_mobility;

CREATE TABLE IF NOT EXISTS users (
    user_id VARCHAR(50) NOT NULL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(100) UNIQUE,
    role VARCHAR(20) NOT NULL DEFAULT 'STUDENT',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS vehicles (
    vehicle_id VARCHAR(50) NOT NULL PRIMARY KEY,
    vehicle_type VARCHAR(50) NOT NULL,
    location VARCHAR(100) NOT NULL,
    battery_level INT NOT NULL DEFAULT 100,
    status VARCHAR(20) NOT NULL DEFAULT 'AVAILABLE',
    user_id VARCHAR(50) NULL,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_vehicle_status (status),
    INDEX idx_vehicle_battery (battery_level),
    CONSTRAINT fk_vehicles_user FOREIGN KEY (user_id) REFERENCES users(user_id)
        ON DELETE SET NULL ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS reservations (
    reservation_id INT AUTO_INCREMENT PRIMARY KEY,
    vehicle_id VARCHAR(50) NOT NULL,
    user_id VARCHAR(50) NOT NULL,
    start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    end_time TIMESTAMP NULL,
    booking_start DATETIME NULL,
    booking_end DATETIME NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    INDEX idx_reservation_status (status),
    INDEX idx_reservation_user (user_id),
    INDEX idx_reservation_vehicle (vehicle_id),
    INDEX idx_reservation_vehicle_status (vehicle_id,status),
    CONSTRAINT fk_reservations_vehicle FOREIGN KEY (vehicle_id) REFERENCES vehicles(vehicle_id)
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fk_reservations_user FOREIGN KEY (user_id) REFERENCES users(user_id)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS maintenance_records (
    maintenance_id INT AUTO_INCREMENT PRIMARY KEY,
    vehicle_id VARCHAR(50) NOT NULL,
    description VARCHAR(500) NOT NULL,
    scheduled_start DATETIME NULL,
    scheduled_end DATETIME NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'SCHEDULED',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at DATETIME NULL,
    INDEX idx_maintenance_vehicle_status (vehicle_id,status),
    CONSTRAINT fk_maintenance_vehicle FOREIGN KEY (vehicle_id) REFERENCES vehicles(vehicle_id)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS notifications (
    notification_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(50) NOT NULL,
    message VARCHAR(1000) NOT NULL,
    notification_type VARCHAR(40) NOT NULL DEFAULT 'INFO',
    related_reservation_id INT NULL,
    is_read TINYINT(1) NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_notifications_user_read (user_id,is_read,created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS mobility_event_outbox (
    outbox_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    event_id VARCHAR(36) NOT NULL UNIQUE,
    event_type VARCHAR(80) NOT NULL,
    payload JSON NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    published_at DATETIME NULL,
    attempts INT NOT NULL DEFAULT 0,
    last_error TEXT NULL,
    INDEX idx_outbox_pending (published_at,outbox_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Seed only missing demo records; existing records are preserved.
INSERT IGNORE INTO users (user_id,name,email,role) VALUES
('STU-1001','Aarav Patel','aarav.patel@campus.edu','STUDENT'),
('STU-1002','Rishi Reddy','rishi.reddy@campus.edu','STUDENT'),
('STU-1003','Sneha Sharma','sneha.sharma@campus.edu','STUDENT'),
('STU-1004','Vikram Rao','vikram.rao@campus.edu','STUDENT'),
('FAC-2001','Dr. Meera Iyer','meera.iyer@campus.edu','FACULTY');

INSERT IGNORE INTO vehicles (vehicle_id,vehicle_type,location,battery_level,status,user_id) VALUES
('EV-101','ELECTRIC_SCOOTER','North Academic Gate',85,'AVAILABLE',NULL),
('EV-102','ELECTRIC_SCOOTER','Student Center Hub',18,'AVAILABLE',NULL),
('EV-103','ELECTRIC_CAR','Main Administration Plaza',92,'AVAILABLE',NULL),
('BK-201','SMART_BICYCLE','Engineering Block B',100,'AVAILABLE',NULL),
('BK-202','SMART_BICYCLE','Central Sports Complex',100,'AVAILABLE',NULL),
('EV-104','ELECTRIC_SCOOTER','Hostel Block 3',15,'AVAILABLE',NULL),
('BK-203','SMART_BICYCLE','Campus Library South',95,'AVAILABLE',NULL);
