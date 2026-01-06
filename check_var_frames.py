import serial
import time
import struct
import sys
import threading
import csv
import msvcrt
from datetime import datetime

# ==============================================================================
# 1. CẤU HÌNH HỆ THỐNG
# ==============================================================================
SERIAL_PORT = 'COM2'
BAUD_RATE = 115200

SOF = b'\xA5\x5A'
PROTOCOL_VER = 0x01
TYPE_STATUS, TYPE_DATA, TYPE_COMMAND, TYPE_ACK = 1, 2, 3, 4
CMD_GET_STATUS, CMD_START_MEASURE, CMD_STOP_MEASURE = 1, 2, 3


# ==============================================================================
# 2. HÀM CRC16
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
# 3. CLASS GIAO TIẾP
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

        # [MOI] CHE DO DEBUG (SOI FRAME)
        self.debug_mode = False

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

    # --- TOGGLE DEBUG ---
    def toggle_debug(self):
        self.debug_mode = not self.debug_mode
        state = "ON (Hien thi Hex)" if self.debug_mode else "OFF (Giau Hex)"
        sys.stdout.write(f"\n>>> [DEBUG MODE] {state}\n")

    # --- FILE RECORDING ---
    def start_recording(self):
        if self.is_recording: return
        timestamp_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.filename = f"data_{timestamp_str}.csv"
        try:
            self.csv_file = open(self.filename, mode='w', newline='')
            self.csv_writer = csv.writer(self.csv_file)
            self.csv_writer.writerow(["Timestamp", "ADC", "Volt", "Status", "Time"])
            self.is_recording = True
            sys.stdout.write(f"\n\n>>> [REC] FILE: {self.filename}\n")
        except Exception as e:
            sys.stdout.write(f"\n>>> [ERROR] Tao file loi: {e}\n")

    def stop_recording(self):
        if self.is_recording and self.csv_file:
            self.csv_file.close()
            self.is_recording = False
            self.csv_file = None
            sys.stdout.write(f"\n\n>>> [REC] DA LUU FILE!\n")

    # --- COMMANDS ---
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

        # [MOI] IN RA FRAME GỬI ĐI NẾU ĐANG DEBUG
        if self.debug_mode:
            hex_str = full_frame.hex(' ').upper()
            sys.stdout.write(f"\n[TX] {hex_str}\n")

        self.ser.write(full_frame)

    # --- READER LOOP ---
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

                    # [MOI] IN RA FRAME NHẬN ĐƯỢC NẾU ĐANG DEBUG
                    # Chỉ in Frame điều khiển (ACK, STATUS) hoặc DATA nếu muốn soi kỹ
                    # Ở đây tôi cho in hết để bạn thấy rõ
                    if self.debug_mode:
                        hex_str = frame.hex(' ').upper()
                        # Nếu là gói DATA thì in gọn hơn chút kẻo trôi màn hình
                        if frame[3] == TYPE_DATA:
                            sys.stdout.write(f"\r[RX DATA] {hex_str}     ")
                        else:
                            sys.stdout.write(f"\n[RX CMD]  {hex_str}\n")

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
            sys.stdout.write(f"\n   << [ACK] Cmd:{hex(cmd)} -> {res_str}\n")

        elif msg_type == TYPE_STATUS:
            state = payload[0]
            n_sensors = payload[1]
            sys.stdout.write(f"\n   << [STATUS] State:{state} Sensors:{n_sensors}\n")

        elif msg_type == TYPE_DATA:
            ts, adc_raw = struct.unpack('<IH', payload)
            voltage = adc_raw * 0.00003125
            status = "THA LONG"
            if voltage < 0.60: status = "DA AN"

            if self.is_recording and self.csv_writer:
                try:
                    self.csv_writer.writerow(
                        [ts, adc_raw, f"{voltage:.4f}", status, datetime.now().strftime("%H:%M:%S.%f")[:-3]])
                except:
                    pass

            # Nếu đang debug thì không in dòng đè [DATA] ... để tránh rối
            if not self.debug_mode:
                rec_tag = "[REC] " if self.is_recording else ""
                sys.stdout.write(f"\r{rec_tag}[DATA] TS:{ts}ms | {voltage:.4f}V -> {status}      ")
                sys.stdout.flush()


# ==============================================================================
# 4. CHƯƠNG TRÌNH CHÍNH
# ==============================================================================
if __name__ == "__main__":
    host = BiomechanicsHost(SERIAL_PORT, BAUD_RATE)
    if host.connect():
        host.start_reading()
        time.sleep(1)
        host.send_command(CMD_GET_STATUS)

        print("\n==========================================")
        print("   HE THONG THU THAP DU LIEU FSR V2.0")
        print("==========================================")
        print(" [s] START Measuring   [d] DEBUG MODE (Soi Frame)")
        print(" [x] STOP Measuring    [r] RECORD Data")
        print(" [g] GET Status        [e] END Record")
        print(" [q] QUIT")
        print("------------------------------------------")

        try:
            while True:
                if msvcrt.kbhit():
                    key = msvcrt.getch().lower()

                    if key == b's':
                        sys.stdout.write("\n>>> START...\n")
                        host.send_command(CMD_START_MEASURE)
                    elif key == b'x':
                        sys.stdout.write("\n>>> STOP...\n")
                        host.send_command(CMD_STOP_MEASURE)
                    elif key == b'g':
                        host.send_command(CMD_GET_STATUS)
                    elif key == b'r':
                        host.start_recording()
                    elif key == b'e':
                        host.stop_recording()
                    elif key == b'd':  # <--- PHÍM MỚI
                        host.toggle_debug()
                    elif key == b'q':
                        break
                time.sleep(0.05)
        except KeyboardInterrupt:
            pass
        finally:
            host.disconnect()