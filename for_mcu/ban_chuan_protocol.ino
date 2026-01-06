#include <Wire.h>
#include <Adafruit_ADS1X15.h>

// ================================================================
// 1. CẤU HÌNH PROTOCOL (CỐ ĐỊNH THEO PROTOCOL.MD)
// ================================================================
const uint8_t SOF_1 = 0xA5;         //
const uint8_t SOF_2 = 0x5A;
const uint8_t PROTOCOL_VER = 0x01;  //

// Message Types
enum MsgType {
  TYPE_STATUS  = 0x01,
  TYPE_DATA    = 0x02,
  TYPE_COMMAND = 0x03,
  TYPE_ACK     = 0x04,
  TYPE_ERROR   = 0x05
};

// Command IDs
enum CmdID {
  CMD_GET_STATUS    = 0x01,
  CMD_START_MEASURE = 0x02,
  CMD_STOP_MEASURE  = 0x03
  // Các lệnh SET khác chưa implement trong bản này
};

// ================================================================
// 2. CẤU HÌNH PHẦN CỨNG
// ================================================================
Adafruit_ADS1115 ads;
const int LED_PIN = 2; // Đèn LED xanh trên ESP32
const float R_FIX = 1000.0; 
const float VCC   = 3.3;

// Trạng thái thiết bị
// 0x00=IDLE, 0x01=MEASURING
uint8_t deviceState = 0x00; 

// Timer
unsigned long lastHeartbeat = 0;  
unsigned long lastSampleTime = 0; 

// ================================================================
// 3. XỬ LÝ CRC16-CCITT (Poly 0x1021)
// ================================================================
uint16_t calculateCRC16(const uint8_t *data, size_t len) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < len; i++) {
    crc ^= ((uint16_t)data[i] << 8);
    for (uint8_t j = 0; j < 8; j++) {
      if (crc & 0x8000) crc = (crc << 1) ^ 0x1021;
      else crc <<= 1;
    }
  }
  return crc;
}

// ================================================================
// 4. MÁY TRẠNG THÁI NHẬN SERIAL (RX STATE MACHINE)
// ================================================================
uint8_t rxBuffer[256]; 
uint16_t rxIndex = 0;
uint16_t expectedPayloadLen = 0;

enum RxState {
  WAIT_SOF1, WAIT_SOF2, READ_HEADER, READ_PAYLOAD, READ_CRC
};
RxState currentState = WAIT_SOF1;
uint8_t headerBytes[4]; 

// ================================================================
// 5. CÁC HÀM GỬI (TX FUNCTIONS)
// ================================================================

// Hàm gửi Frame tổng quát theo cấu trúc mục 2
void sendRawFrame(uint8_t type, uint8_t *payload, uint16_t len) {
  // 1. Gửi SOF
  Serial.write(SOF_1);
  Serial.write(SOF_2);
  
  // 2. Chuẩn bị dữ liệu để tính CRC (Ver | Type | Len | Payload)
  // Len field là 2 bytes Little-Endian
  uint8_t *crcBuf = (uint8_t*)malloc(4 + len);
  if (crcBuf == NULL) return; // Tránh lỗi thiếu RAM

  crcBuf[0] = PROTOCOL_VER;
  crcBuf[1] = type;
  crcBuf[2] = len & 0xFF;        // Low byte
  crcBuf[3] = (len >> 8) & 0xFF; // High byte
  memcpy(crcBuf + 4, payload, len);
  
  // Tính CRC
  uint16_t crc = calculateCRC16(crcBuf, 4 + len);
  
  // 3. Gửi phần Header (Ver, Type, Len)
  Serial.write(crcBuf, 4); 
  
  // 4. Gửi Payload
  if (len > 0) {
    Serial.write(payload, len);
  }
  
  // 5. Gửi CRC (Little-endian)
  Serial.write(crc & 0xFF);
  Serial.write((crc >> 8) & 0xFF);
  
  free(crcBuf);
}

// Gửi ACK Frame
void sendAck(uint8_t cmdID, uint8_t seq, uint8_t result) {
  // Payload: CmdID(1) | Seq(1) | Result(1)
  uint8_t ackPayload[3] = {cmdID, seq, result};
  sendRawFrame(TYPE_ACK, ackPayload, 3);
}

// Gửi STATUS Frame
void sendStatusFrame() {
  // Tổng size STATUS là 144 bytes
  // Ở đây chúng ta tạo mảng tĩnh để tiết kiệm thời gian cấp phát
  static uint8_t statusPayload[144]; 
  
  // Xóa trắng toàn bộ trước khi điền
  memset(statusPayload, 0, 144);
  
  // Điền các trường quan trọng
  statusPayload[0] = deviceState; // State
  statusPayload[1] = 1;           // NSensors (1 sensor active)
  
  // Các trường ActiveMap, HealthMap, SampRateMap... tạm để 0
  // (Sau này nếu cần config thật thì điền vào đây)

  sendRawFrame(TYPE_STATUS, statusPayload, 144);
}

// ================================================================
// 6. XỬ LÝ LỆNH (COMMAND HANDLER)
// ================================================================
void handleFrame(uint8_t type, uint8_t *payload, uint16_t len) {
  // Chỉ xử lý gói COMMAND (Type 0x03)
  if (type == TYPE_COMMAND) {
    if (len < 2) return; // Payload tối thiểu phải có CmdID và Seq
    
    uint8_t cmdID = payload[0];
    uint8_t seq   = payload[1];
    // Args bắt đầu từ payload[2]
    
    switch (cmdID) {
      case CMD_GET_STATUS:
        sendAck(cmdID, seq, 0x00); // OK
        sendStatusFrame();         // Trả lời bằng bảng Status
        break;
        
      case CMD_START_MEASURE:
        deviceState = 0x01;          // Chuyển sang MEASURING
        digitalWrite(LED_PIN, HIGH); // Bật đèn báo
        sendAck(cmdID, seq, 0x00);   // OK
        break;
        
      case CMD_STOP_MEASURE:
        deviceState = 0x00;          // Chuyển về IDLE
        digitalWrite(LED_PIN, LOW);  // Tắt đèn báo
        sendAck(cmdID, seq, 0x00);   // OK
        break;
        
      default:
        sendAck(cmdID, seq, 0x01); // 0x01 = INVALID_COMMAND
        break;
    }
  }
}

// Máy trạng thái xử lý từng byte từ Serial
void processIncomingByte(uint8_t byte) {
  switch (currentState) {
    case WAIT_SOF1:
      if (byte == SOF_1) currentState = WAIT_SOF2;
      break;

    case WAIT_SOF2:
      if (byte == SOF_2) {
        rxIndex = 0;
        currentState = READ_HEADER;
      } else currentState = WAIT_SOF1;
      break;

    case READ_HEADER:
      rxBuffer[rxIndex++] = byte;
      if (rxIndex == 4) {
        // Parse độ dài Payload (Little Endian)
        expectedPayloadLen = rxBuffer[2] | (rxBuffer[3] << 8);
        
        // Lưu header để tính CRC sau này
        memcpy(headerBytes, rxBuffer, 4);
        
        rxIndex = 0;
        // Nếu payload = 0 thì nhảy cóc sang check CRC luôn
        currentState = (expectedPayloadLen > 0) ? READ_PAYLOAD : READ_CRC;
      }
      break;

    case READ_PAYLOAD:
      rxBuffer[rxIndex++] = byte;
      if (rxIndex == expectedPayloadLen) {
        currentState = READ_CRC;
        rxIndex = 0;
      }
      break;

    case READ_CRC:
      static uint8_t crcBytes[2];
      crcBytes[rxIndex++] = byte;
      
      if (rxIndex == 2) {
        // 1. Lấy CRC từ gói tin
        uint16_t receivedCRC = crcBytes[0] | (crcBytes[1] << 8);
        
        // 2. Tính toán CRC lại từ dữ liệu đã nhận
        uint8_t *checkBuf = (uint8_t*)malloc(4 + expectedPayloadLen);
        if (checkBuf != NULL) {
            memcpy(checkBuf, headerBytes, 4);
            if (expectedPayloadLen > 0) {
                memcpy(checkBuf + 4, rxBuffer, expectedPayloadLen);
            }
            
            uint16_t calcCRC = calculateCRC16(checkBuf, 4 + expectedPayloadLen);
            free(checkBuf);

            // 3. So khớp
            if (receivedCRC == calcCRC) {
              handleFrame(headerBytes[1], rxBuffer, expectedPayloadLen);
            }
        }
        currentState = WAIT_SOF1; // Reset về chờ gói mới
      }
      break;
  }
}

// ================================================================
// 7. SETUP & LOOP
// ================================================================
void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  
  // Cấu hình ADC ADS1115
  // GAIN_FOUR: +/- 1.024V 1 bit = 0.03125mV
  ads.setGain(GAIN_FOUR); 
  
  if (!ads.begin()) {
    // Nếu lỗi phần cứng, nháy đèn liên tục báo nguy
    while(1) { 
        digitalWrite(LED_PIN, HIGH); delay(50); 
        digitalWrite(LED_PIN, LOW); delay(50); 
    }
  }
  
  // Khi mới khởi động, gửi ngay 1 gói Status để báo danh
  sendStatusFrame();
}

void loop() {
  // --- A. NHẬN DỮ LIỆU (Non-blocking) ---
  while (Serial.available()) {
    processIncomingByte(Serial.read());
  }
  
  unsigned long currentMillis = millis();

  // --- B. LOGIC TRẠNG THÁI ---
  if (deviceState == 0x01) { 
    // === CHẾ ĐỘ ĐO (MEASURING) ===
    // Gửi DATA tốc độ 10Hz (100ms/lần)
    if (currentMillis - lastSampleTime > 100) { 
      lastSampleTime = currentMillis;
      
      int16_t adc = ads.readADC_SingleEnded(3); // Đọc chân A3
      
      // Đóng gói Payload DATA: Timestamp(4) + ADC(2)
      // Lưu ý: Protocol yêu cầu Little Endian
      uint8_t dataPayload[6];
      uint32_t ts = currentMillis;
      
      dataPayload[0] = ts & 0xFF;
      dataPayload[1] = (ts >> 8) & 0xFF;
      dataPayload[2] = (ts >> 16) & 0xFF;
      dataPayload[3] = (ts >> 24) & 0xFF;
      
      dataPayload[4] = adc & 0xFF;
      dataPayload[5] = (adc >> 8) & 0xFF; // ADS1115 trả về int16
      
      sendRawFrame(TYPE_DATA, dataPayload, 6);
    }
    
  } else {
    // === CHẾ ĐỘ RẢNH (IDLE) ===
    // Gửi Heartbeat STATUS định kỳ
    // Chọn 3000ms (3s) để tối ưu băng thông (nằm trong khoảng 0.1-1Hz cho phép)
    if (currentMillis - lastHeartbeat > 3000) {
      lastHeartbeat = currentMillis;
      sendStatusFrame(); 
    }
  }
}