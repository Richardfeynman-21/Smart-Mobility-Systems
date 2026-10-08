-- =======================================================
-- Smart Campus Mobility Database Schema
-- Compatible with MySQL 8.0+ / Amazon RDS MySQL
-- =======================================================

CREATE DATABASE IF NOT EXISTS smart_campus_mobility
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE smart_campus_mobility;

-- Disable foreign key checks for clean teardown and rebuild
SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS reservations;
DROP TABLE IF EXISTS vehicles;
DROP TABLE IF EXISTS users;
SET FOREIGN_KEY_CHECKS = 1;

-- =======================================================
-- 1. USERS TABLE
-- =======================================================
CREATE TABLE users (
    user_id VARCHAR(50) NOT NULL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(100) UNIQUE,
    role ENUM('STUDENT', 'FACULTY', 'STAFF', 'ADMIN') DEFAULT 'STUDENT',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =======================================================
-- 2. VEHICLES TABLE
-- =======================================================
CREATE TABLE vehicles (
    vehicle_id VARCHAR(50) NOT NULL PRIMARY KEY,
    vehicle_type VARCHAR(50) NOT NULL,
    location VARCHAR(100) NOT NULL,
    battery_level INT NOT NULL DEFAULT 100,
    status ENUM('AVAILABLE', 'RESERVED', 'IN_USE', 'MAINTENANCE') NOT NULL DEFAULT 'AVAILABLE',
    user_id VARCHAR(50) NULL,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_vehicles_user FOREIGN KEY (user_id) 
        REFERENCES users(user_id) 
        ON DELETE SET NULL 
        ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- =======================================================
-- 3. RESERVATIONS TABLE
-- =======================================================
CREATE TABLE reservations (
    reservation_id INT AUTO_INCREMENT PRIMARY KEY,
    vehicle_id VARCHAR(50) NOT NULL,
    user_id VARCHAR(50) NOT NULL,
    start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    end_time TIMESTAMP NULL,
    status ENUM('ACTIVE', 'COMPLETED', 'CANCELLED') NOT NULL DEFAULT 'ACTIVE',
    CONSTRAINT fk_reservations_vehicle FOREIGN KEY (vehicle_id) 
        REFERENCES vehicles(vehicle_id) 
        ON DELETE CASCADE 
        ON UPDATE CASCADE,
    CONSTRAINT fk_reservations_user FOREIGN KEY (user_id) 
        REFERENCES users(user_id) 
        ON DELETE CASCADE 
        ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Useful indexes for querying
CREATE INDEX idx_vehicle_status ON vehicles(status);
CREATE INDEX idx_vehicle_battery ON vehicles(battery_level);
CREATE INDEX idx_reservation_status ON reservations(status);
CREATE INDEX idx_reservation_user ON reservations(user_id);
CREATE INDEX idx_reservation_vehicle ON reservations(vehicle_id);

-- =======================================================
-- SEED SAMPLE DATA
-- =======================================================

-- Users / Students
INSERT INTO users (user_id, name, email, role) VALUES
('STU-1001', 'Jithesh', 'aarav.patel@campus.edu', 'STUDENT'),
('STU-1002', 'Rishi Reddy', 'rishi.reddy@campus.edu', 'STUDENT'),
('STU-1003', 'Sneha Sharma', 'sneha.sharma@campus.edu', 'STUDENT'),
('STU-1004', 'Vikram Rao', 'vikram.rao@campus.edu', 'STUDENT'),
('FAC-2001', 'Dr. Meera Iyer', 'meera.iyer@campus.edu', 'FACULTY');

-- Vehicles (EVs, Scooters, Bicycles)
INSERT INTO vehicles (vehicle_id, vehicle_type, location, battery_level, status, user_id) VALUES
('EV-101', 'ELECTRIC_SCOOTER', 'North Academic Gate', 85, 'AVAILABLE', NULL),
('EV-102', 'ELECTRIC_SCOOTER', 'Student Center Hub', 18, 'AVAILABLE', NULL),
('EV-103', 'ELECTRIC_CAR', 'Main Administration Plaza', 92, 'AVAILABLE', NULL),
('BK-201', 'SMART_BICYCLE', 'Engineering Block B', 100, 'AVAILABLE', NULL),
('BK-202', 'SMART_BICYCLE', 'Central Sports Complex', 100, 'RESERVED', 'STU-1001'),
('EV-104', 'ELECTRIC_SCOOTER', 'Hostel Block 3', 15, 'MAINTENANCE', NULL),
('BK-203', 'SMART_BICYCLE', 'Campus Library South', 95, 'AVAILABLE', NULL);

-- Active & Historical Reservations
INSERT INTO reservations (reservation_id, vehicle_id, user_id, start_time, end_time, status) VALUES
(1, 'BK-202', 'STU-1001', CURRENT_TIMESTAMP, NULL, 'ACTIVE');
