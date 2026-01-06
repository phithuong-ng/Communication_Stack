
## 22-12-2025 (Final Draft)

# Communication Stack for Biomechanics Device.

## Protocol Version 1 (Final)

This document defines the binary communication protocol used between the
rehabilitation device MCU and the host computer.

**Key Revisions from Draft:**

* `Len` field expanded to 2 bytes for larger payload capacity.
* **Per-Sensor Sampling Rates** are introduced via `SampRateMap`.
* A dedicated **`ERROR`** frame is defined for asynchronous fault reporting.

---

## 1. Byte Order

All multi-byte numeric fields SHALL be encoded in **little-endian** byte order.

---

## 2. Frame Format (Overall)

All communication is performed using binary frames with the following format:

| Field | SOF | Ver | Type | **Len** | Payload | CRC16 |
| --- | --- | --- | --- | --- | --- | --- |
| **Size** | 2 B | 1 B | 1 B | **2 B** | N bytes | 2 B |

Frames are transmitted byte-by-byte from left to right.

---

## 3. Field Definitions

### 3.1 SOF — Start of Frame (2 bytes)

* Fixed value: `0xA5 0x5A`
* Not included in CRC calculation.

### 3.2 Ver — Protocol Version (1 byte)

* Current version: `0x01`
* Included in CRC calculation.

### 3.3 Type — Message Type (1 byte)

| Value (hex) | Name | Direction |
| --- | --- | --- |
| `0x01` | STATUS | Device  Host |
| `0x02` | DATA | Device  Host |
| `0x03` | COMMAND | Host  Device |
| `0x04` | ACK | Device  Host |
| `0x05` | **ERROR** | Device  Host |

### 3.4 Len — Payload Length (**2 bytes, little-endian**)

* **Revised:** Increased size to support large data frames.
* Number of bytes in the Payload field.
* Range: 0–65535.
* Included in CRC calculation.

### 3.5 Payload — Frame Data (N bytes)

* Included in CRC calculation.

### 3.6 CRC16 — Frame Check Sequence (2 bytes)

* CRC-16-CCITT, Polynomial: `0x1021`, Initial value: `0xFFFF`.
* Calculated over: `Ver | Type | Len | Payload`.

---

## 4. General Rules

* Frames with invalid CRC SHALL be discarded.
* The frame envelope format is fixed and MUST NOT change between versions.

---

## 5. STATUS FRAME (Type `0x01`)

STATUS frames reflect the current authoritative device state.

### 5.1 STATUS Frame Transmission Rules

The device SHALL transmit a STATUS frame after boot, periodically (0.1–1 Hz), in response to a request, and after any successful configuration or state change.

### 5.2 Field Definitions

| Field | Size | Description |
| --- | --- | --- |
| **State** | 1 byte | Operational state (0x00=IDLE, 0x01=MEASURING, 0x02=CALIBRATING, 0x03=ERROR). |
| **NSensors** | 1 byte | Number of **active** sensors (must equal bits set in `ActiveMap`). Range: 0–32. |
| **ActiveMap** | 4 bytes | Bitmap indicating enabled sensors (Bit  is enabled). |
| **HealthMap** | 4 bytes | Bitmap indicating sensor health (Bit  is healthy). |
| **SampRateMap** | **64 bytes** | **Per-sensor sampling rate in Hz.** `SampRateMap[i]` = rate for sensor index . (uint16, 0–65,535). |
| **BitsPerSmpMap** | **32 bytes**| Array where Index N stores the resolution for Sensor N |
| **SensorRoleMap** | 32 bytes | Semantic role of each sensor channel (1 byte per sensor index, see Role Table). |
| **ADCFlags** | 2 bytes | Bitfield reporting ADC/acquisition subsystem status. |
| **Reserved** | 2 bytes | Set to zero. |

**Total STATUS Payload Size:** 144 bytes.

### 5.3 STATUS Semantics

* **NSensors** SHALL equal the number of bits set in **ActiveMap**.
* The Host MUST use **SampRateMap** to determine data timing and expected bandwidth.

---

## 6. DATA Payload Format (Type `0x02`)

The DATA frame carries raw ADC samples from all active sensors.

### 6.1 DATA Payload Layout

| Field | Size |
| --- | --- |
| Timestamp | 4 bytes |
| Samples | N bytes |

### 6.3 Samples Field Ordering

* Samples are transmitted in **ascending sensor index order of the active channels**.
* The -th sample in the payload corresponds to the -th set bit in the `ActiveMap`.


### 6.4: Data Payload - Sample Encoding

The `DATA` frame payload contains a 4-byte `Timestamp` followed by the raw sensor samples. The encoding of each sensor sample is determined by the `BitsPerSmp` field provided in the last transmitted `STATUS` frame. All multi-byte values (Timestamp, Samples) MUST be encoded in **Little-Endian** byte order.

To maintain simplicity and high decoding speed, all samples are padded to the nearest required byte boundary based on the resolution, following the rules below. The receiver must use the `BitsPerSmp` value to apply the correct bitmask and extract the true sensor reading.

| BitsPerSmp (Resolution) | Bytes per sample | Total Bits Padded | Notes |
| --- | --- | --- | --- | --- |
| **1–8** | **1 byte** | 8 | Used for boolean or low-res data. |
| **9–16** | **2 bytes** | 16 | Covers standard 10, 12, 14, 16-bit ADCs. |
| **17–24** | **3 bytes** | 24 | Covers up to 24-bit ADCs. |
| **25–32** | **4 bytes** | 32 | Supports high-resolution and 32-bit data. |

---

## 7. COMMAND Payload Format (Type `0x03`)

COMMAND frames are sent from the host.

### 7.1 COMMAND Payload Layout

| Field | Size | Description |
| --- | --- | --- |
| CmdID | 1 byte | Command ID |
| Seq | 1 byte | Sequence number |
| Args | N bytes | Command arguments |

### 7.2 Command IDs (Updated)

| CmdID (hex) | Name | Description |
| --- | --- | --- |
| `0x01` | GET_STATUS | Request a STATUS frame |
| `0x02` | START_MEASURE | Start data acquisition |
| `0x03` | STOP_MEASURE | Stop data acquisition |
| `0x04` | SET_NSENSORS | Set number of sensors (maximum active) |
| `0x05` | SET_RATE | Set the sampling rate for a single sensor. |
| `0x06` | SET_BITS | Set the bit resolution for a single sensor. |
| `0x07` | SET_ACTIVEMAP | Enable/disable sensors |
| `0x08` | CALIBRATE | Start calibration |

### 7.4 Command Arguments

| CmdID | Args Format | Notes |
| --- | --- | --- |
| GET_STATUS, START_MEASURE, STOP_MEASURE | none |  |
| SET_NSENSORS | `uint8 NSensors` |  |
| SET_RATE | `uint8 SensorIndex`, `uint16 SampRateHz` | Sets the rate (0-65,535 Hz) for the specified sensor index (0-31). |
| SET_BITS | `unit8 SensorIndex`, `uint8 BitsPerSmp` | Set the resolution (0-255, usually 10-24 bits) for the specified sensor index (0-31) |
| SET_ACTIVEMAP | `uint32 ActiveMap` | 4 bytes, maps to sensor indices 0-31. |
| CALIBRATE | `uint8 Mode` |  |

### 7.5 COMMAND Semantics

* Invalid or unsupported commands SHALL result in a negative **ACK**.
* Configuration changes SHALL result in a STATUS frame *after* the ACK.

---

## 8. ACK Payload Format (Type `0x04`)

ACK frames are sent by the device in synchronous response to a **COMMAND**.

### 8.1 ACK Payload Layout

| Field | Size | Description |
| --- | --- | --- |
| CmdID | 1 byte | Command ID being acknowledged. |
| Seq | 1 byte | Sequence number from the command. |
| Result | 1 byte | Result code (0x00 = OK). |

### 8.2 Result Codes

| Result (hex) | Meaning |
| --- | --- |
| `0x00` | OK |
| `0x01` | INVALID_COMMAND |
| `0x02` | INVALID_ARGUMENT |
| `0x03` | BUSY |
| `0x04` | FAILED |
| `0x05` | NOT_ALLOWED |

---

## 9. ERROR Payload Format (Type `0x05`)


### 9.1 ERROR Payload Layout

| Field | Size | Description |
| --- | --- | --- |
| Timestamp | 4 bytes | Microseconds since device start. |
| ErrCode | 1 byte | System error type. |
| AuxData | 2 bytes | Context-dependent auxiliary data (e.g., sensor index, flag mask). |

### 9.2 Error Codes (ErrCode)

| ErrCode (hex) | Meaning | Trigger / Context |
| --- | --- | --- |
| `0x01` | ADC_OVERRUN | ADC data was lost due to processing speed. |
| `0x02` | SENSOR_FAULT | A sensor has become permanently faulty or disconnected. |
| `0x03` | FIFO_CRITICAL | Internal data buffer is full/overflowing. |
| `0x04` | LOW_VOLTAGE | System power is below operating threshold. |
| `0xFE` | VENDOR_SPECIFIC | Vendor defined critical error. |

---

### Work

The work is defined as such:

| System | Primary Function | Frame Types Handled | Complexity | Language |
| --- | --- | --- | --- | --- |
| **MCU (Device)** | **Maker** | `STATUS`, `DATA`, `ACK`, `ERROR` | High | C/Rust |
|  | **Parser** | **`COMMAND`** (Only) | Low (Fixed format) | C/Rust |
| **Host (Computer)** | **Parser** | **`STATUS`, `DATA`, `ACK`, `ERROR**` (All Device-to-Host frames) | High (Context-aware) | Python |
|  | **Maker** | `COMMAND` (Only) | Low | Python |

