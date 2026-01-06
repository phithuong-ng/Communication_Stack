import serial
import time
import struct
import sys
import threading
import csv
import msvcrt  # <--- [QUAN TRỌNG] Thư viện bắt phím trên Windows
from datetime import datetime

# ==============================================================================
# 1. CẤU HÌNH HỆ THỐNG
# ==============================================================================
SERIAL_PORT = 'COM2'
BAUD_RATE = 115200

# Constants
SOF = b'\xA5\x5A'
PROTOCOL_VER = 0x01

# Frame Types
TYPE_STATUS = 0x01
TYPE_DATA = 0x02
TYPE_COMMAND = 0x03
TYPE_ACK = 0x04

# Command IDs
CMD_GET_STATUS = 0x01
CMD_START_MEASURE = 0x02
CMD_STOP_MEASURE = 0x03


# ==============================================================================
# 2. HÀM TIỆN ÍCH (CRC16)
# ==============================================================================
def calculate_crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= (byte << 8)
        for _ in range(8):
            if (crc & 0x8000):
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
        crc &= 0xFFFF
    return crc


# ==============================================================================
# 3. CLASS GIAO TIẾP (GIỮ NGUYÊN)
# ==============================================================================
class BiomechanicsHost:
    def __init__(self, port, baud):
        self.port = port
        self.baud = baud
        self.ser = None
        self.seq_counter = 0
        self.running = False
        self.read_thread = None

        # Biến ghi file
        self.is_recording = False
        self.csv_file = None
        self.csv_writer = None
        self.filename = ""

    def connect(self):
        try:
            try:
                s = serial.Serial(self.port, self.baud); s.close()
            except:
                pass
            self.ser = serial.Serial(self.port, self.baud, timeout=0.1)
            print(f">> [SYSTEM] Da ket noi {self.port} (Ver {PROTOCOL_VER})")
            return True
        except Exception as e:
            print(f">> [ERROR] Loi ket noi: {e}")
            return False

    def disconnect(self):
        self.stop_recording()
        self.running = False
        if self.read_thread: self.read_thread.join()
        if self.ser and self.ser.is_open:
            self.ser.close()
            print("\n>> [SYSTEM] Da ngat ket noi.")

    # --- FILE RECORDING ---
    def start_recording(self):
        if self.is_recording: return
        timestamp_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.filename = f"sensor_data_{timestamp_str}.csv"
        try:
            self.csv_file = open(self.filename, mode='w', newline='')
            self.csv_writer = csv.writer(self.csv_file)
            self.csv_writer.writerow(["Timestamp_MCU_ms", "ADC_Raw", "Voltage_V", "Status", "PC_Time"])
            self.is_recording = True
            # In xuống dòng mới để không bị data đè
            sys.stdout.write(f"\n>> [REC] BAT DAU GHI FILE: {self.filename}\n")
        except Exception as e:
            sys.stdout.write(f"\n>> [ERROR] Khong the tao file: {e}\n")

    def stop_recording(self):
        if self.is_recording and self.csv_file:
            self.csv_file.close()
            self.is_recording = False
            self.csv_file = None
            self.csv_writer = None
            sys.stdout.write(f"\n>> [REC] DA LUU FILE: {self.filename}\n")

    # --- SEND COMMAND ---
    def send_command(self, cmd_id, args=b''):
        if not self.ser: return
        self.seq_counter = (self.seq_counter + 1) % 256
        cmd_payload = struct.pack('<BB', cmd_id, self.seq_counter) + args
        self._send_raw_frame(TYPE_COMMAND, cmd_payload)

    def _send_raw_frame(self, msg_type, payload):
        payload_len = len(payload)
        header_for_crc = struct.pack('<BBH', PROTOCOL_VER, msg_type, payload_len)
        data_to_crc = header_for_crc + payload
        crc = calculate_crc16(data_to_crc)
        full_frame = SOF + data_to_crc + struct.pack('<H', crc)
        self.ser.write(full_frame)

    # --- RECEIVE LOOP ---
    def start_reading(self):
        self.running = True
        self.read_thread = threading.Thread(target=self._reader_loop)
        self.read_thread.daemon = True
        self.read_thread.start()

    def _reader_loop(self):
        buffer = b''
        while self.running and self.ser.is_open:
            try:
                if self.ser.in_waiting:
                    buffer += self.ser.read(self.ser.in_waiting)

                while len(buffer) >= 6:
                    sof_index = buffer.find(SOF)
                    if sof_index == -1: buffer = b''; break
                    if sof_index > 0: buffer = buffer[sof_index:]
                    if len(buffer) < 6: break

                    payload_len = struct.unpack_from('<H', buffer, 4)[0]
                    total_len = 6 + payload_len + 2
                    if len(buffer) < total_len: break

                    frame = buffer[:total_len]
                    buffer = buffer[total_len:]
                    self._process_frame(frame, payload_len)
                time.sleep(0.005)
            except Exception:
                break

    def _process_frame(self, frame, payload_len):
        msg_type = frame[3]
        payload = frame[6: 6 + payload_len]

        if msg_type == TYPE_ACK:
            cmd, seq, res = struct.unpack('<BBB', payload)
            res_str = "OK" if res == 0 else f"FAIL({res})"
            # In xuống dòng để dễ nhìn ACK
            sys.stdout.write(f"\n   << [ACK] Cmd: {hex(cmd)} -> {res_str}\n")

        elif msg_type == TYPE_STATUS:
            state = payload[0]
            n_sensors = payload[1]
            state_map = {0: "IDLE", 1: "MEASURING", 2: "CALIB", 3: "ERROR"}
            sys.stdout.write(f"\n   << [STATUS] State: {state_map.get(state, 'Unknown')} | Active: {n_sensors}\n")

        elif msg_type == TYPE_DATA:
            ts, adc_raw = struct.unpack('<IH', payload)
            voltage = adc_raw * 0.00003125
            status = "THA LONG"
            if voltage < 0.60: status = "DA AN"

            # Ghi file nền
            rec_tag = ""
            if self.is_recording and self.csv_writer:
                rec_tag = "[REC] "
                try:
                    self.csv_writer.writerow([
                        ts, adc_raw, f"{voltage:.4f}", status,
                        datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    ])
                except:
                    pass

            # HIỂN THỊ REALTIME (Ghi đè dòng cũ)
            # Dùng sys.stdout.write với \r để đưa con trỏ về đầu dòng
            sys.stdout.write(f"\r{rec_tag}[DATA] TS: {ts}ms | {voltage:.4f}V -> {status}        ")
            sys.stdout.flush()


# ==============================================================================
# 4. CHƯƠNG TRÌNH CHÍNH (SỬ DỤNG MSVCRT - KHÔNG CẦN ENTER)
# ==============================================================================
if __name__ == "__main__":
    host = BiomechanicsHost(SERIAL_PORT, BAUD_RATE)

    if host.connect():
        host.start_reading()
        time.sleep(1)
        host.send_command(CMD_GET_STATUS)

        print("\n--- DIEU KHIEN TUC THOI (KHONG CAN ENTER) ---")
        print(" [s] START Measuring")
        print(" [x] STOP Measuring")
        print(" [g] Get Status")
        print(" [r] START Recording")
        print(" [e] END Recording")
        print(" [q] Quit")
        print("---------------------------------------------")

        try:
            while True:
                # Kiểm tra xem có phím nào được ấn không
                if msvcrt.kbhit():
                    # Lấy phím vừa ấn (dạng byte), decode ra chuỗi
                    key = msvcrt.getch().decode('utf-8').lower()

                    if key == 's':
                        sys.stdout.write("\n>> Gui lenh START...\n")
                        host.send_command(CMD_START_MEASURE)

                    elif key == 'x':
                        sys.stdout.write("\n>> Gui lenh STOP...\n")
                        host.send_command(CMD_STOP_MEASURE)

                    elif key == 'g':
                        sys.stdout.write("\n>> Gui lenh GET STATUS...\n")
                        host.send_command(CMD_GET_STATUS)

                    elif key == 'r':
                        host.start_recording()

                    elif key == 'e':
                        host.stop_recording()

                    elif key == 'q':
                        sys.stdout.write("\n>> Tam biet!\n")
                        break

                # Nghỉ cực ngắn để không ngốn CPU
                time.sleep(0.05)

        except KeyboardInterrupt:
            pass
        finally:
            host.disconnect()