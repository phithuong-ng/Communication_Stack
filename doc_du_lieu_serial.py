import serial
import time

# Đảm bảo COM7 là đúng cổng của ESP32
ser = serial.Serial('COM7', 115200, timeout=1)

print(f"Đang nghe tại {ser.port}...")

# --- THÊM ĐOẠN NÀY ---
time.sleep(2) # Đợi ESP32 khởi động xong sau khi mở cổng
ser.write(b's') # Gửi lệnh 's' để kích hoạt chế độ MEASURING
print("Đã gửi lệnh kích hoạt 's'...")
# ---------------------

while True:
    if ser.in_waiting > 0:
        # Đọc dữ liệu thô
        raw_data = ser.read(ser.in_waiting)
        # In ra mã HEX
        hex_string = ' '.join(f'{b:02X}' for b in raw_data)
        print(hex_string)
    time.sleep(0.1)