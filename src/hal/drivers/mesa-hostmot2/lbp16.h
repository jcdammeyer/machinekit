
#ifndef __LBP16_H
#define __LBP16_H

#define LBP16_SENDRECV_DEBUG 0

#define LBP16_MEM_SPACE_COUNT 8

#define LBP16_CMD_SIZE  2
#define LBP16_ADDR_SIZE 2
#define LBP16_CMDADDR_PACKET_SIZE (LBP16_CMD_SIZE + LBP16_ADDR_SIZE)
#define LBP16_CMDONLY_PACKET_SIZE (LBP16_CMD_SIZE)

#define FLASH_ADDR_REG      0x0000
#define FLASH_DATA_REG      0x0004
#define FLASH_ID_REG        0x0008
#define FLASH_SEC_ERASE_REG 0x000C

#define ETH_EEPROM_IP_REG   0x0020

#define COMM_CTRL_WRITE_ENA_REG 0x001A

#define LBP_ADDR_AUTO_INC     0x0080
#define LBP_ARGS_8BIT         0x0000
#define LBP_ARGS_16BIT        0x0100
#define LBP_ARGS_32BIT        0x0200
#define LBP_ARGS_64BIT        0x0300
#define LBP_SPACE_HM2         0x0000
#define LBP_SPACE_ETH_CHIP    0x0400
#define LBP_SPACE_ETH_EEPROM  0x0800
#define LBP_SPACE_FPGA_FLASH  0x0C00
#define LBP_SPACE_COMM_CTRL   0x1800
#define LBP_SPACE_BOARD_INFO  0x1C00
#define LBP_SPACE_ACC         0x0000
#define LBP_INFO_ACC          0x2000
#define LBP_READ              0x0000
#define LBP_ADDR              0x4000
#define LBP_NO_ADDR           0x0000
#define LBP_WRITE             0x8000

#define CMD_READ_AREA_INFO_16     (LBP_READ | LBP_ADDR | LBP_INFO_ACC | LBP_ARGS_16BIT)
#define CMD_READ_AREA_INFO_16_INC (CMD_READ_AREA_INFO_16 | LBP_ADDR_AUTO_INC)

#define CMD_READ_AREA_INFO_ADDR16(space, size)       (CMD_READ_AREA_INFO_16 | space | ((size) & 0x7F))
#define CMD_READ_AREA_INFO_ADDR16_INC(space, size)   (CMD_READ_AREA_INFO_16_INC | space | ((size) & 0x7F))

#define CMD_READ_ADDR_16      (LBP_READ | LBP_ADDR | LBP_SPACE_ACC | LBP_ARGS_16BIT)
#define CMD_READ_ADDR_16_INC  (CMD_READ_ADDR_16 | LBP_ADDR_AUTO_INC)
#define CMD_READ_ADDR_32      (LBP_READ | LBP_ADDR | LBP_SPACE_ACC | LBP_ARGS_32BIT)
#define CMD_READ_ADDR_32_INC  (CMD_READ_ADDR_32 | LBP_ADDR_AUTO_INC)
#define CMD_WRITE_ADDR_16     (LBP_WRITE | LBP_ADDR | LBP_SPACE_ACC | LBP_ARGS_16BIT)
#define CMD_WRITE_ADDR_16_INC (CMD_WRITE_ADDR_16 | LBP_ADDR_AUTO_INC)
#define CMD_WRITE_ADDR_32     (LBP_WRITE | LBP_ADDR | LBP_SPACE_ACC | LBP_ARGS_32BIT)

#define CMD_READ_HOSTMOT2_ADDR32(size)        (CMD_READ_ADDR_32 | LBP_SPACE_HM2 | ((size) & 0x7F))
#define CMD_READ_HOSTMOT2_ADDR32_INC(size)    (CMD_READ_ADDR_32_INC | LBP_SPACE_HM2 | ((size) & 0x7F))
#define CMD_READ_ETH_CHIP_ADDR16(size)        (CMD_READ_ADDR_16 | LBP_SPACE_ETH_CHIP | ((size) & 0x7F))
#define CMD_READ_ETH_CHIP_ADDR16_INC(size)    (CMD_READ_ADDR_16_INC | LBP_SPACE_ETH_CHIP | ((size) & 0x7F))
#define CMD_READ_ETH_EEPROM_ADDR16(size)      (CMD_READ_ADDR_16 | LBP_SPACE_ETH_EEPROM | ((size) & 0x7F))
#define CMD_READ_ETH_EEPROM_ADDR16_INC(size)  (CMD_READ_ADDR_16_INC | LBP_SPACE_ETH_EEPROM | ((size) & 0x7F))
#define CMD_READ_FPGA_FLASH_ADDR32(size)      (CMD_READ_ADDR_32 | LBP_SPACE_FPGA_FLASH | ((size) & 0x7F))
#define CMD_READ_COMM_CTRL_ADDR16(size)       (CMD_READ_ADDR_16 | LBP_SPACE_COMM_CTRL | ((size) & 0x7F))
#define CMD_READ_COMM_CTRL_ADDR16_INC(size)   (CMD_READ_ADDR_16_INC | LBP_SPACE_COMM_CTRL | ((size) & 0x7F))
#define CMD_READ_BOARD_INFO_ADDR16(size)      (CMD_READ_ADDR_16 | LBP_SPACE_BOARD_INFO | ((size) & 0x7F))
#define CMD_READ_BOARD_INFO_ADDR16_INC(size)  (CMD_READ_ADDR_16_INC | LBP_SPACE_BOARD_INFO | ((size) & 0x7F))

#define CMD_WRITE_FPGA_FLASH_ADDR32(size)     (CMD_WRITE_ADDR_32 | LBP_SPACE_FPGA_FLASH | ((size) & 0x7F))
#define CMD_WRITE_COMM_CTRL_ADDR16(size)      (CMD_WRITE_ADDR_16 | LBP_SPACE_COMM_CTRL | ((size) & 0x7F))
#define CMD_WRITE_ETH_EEPROM_ADDR16(size)     (CMD_WRITE_ADDR_16 | LBP_SPACE_ETH_EEPROM | ((size) & 0x7F))
#define CMD_WRITE_ETH_EEPROM_ADDR16_INC(size) (CMD_WRITE_ADDR_16_INC | LBP_SPACE_ETH_EEPROM | ((size) & 0x7F))
#define CMD_WRITE_HOSTMOT2_ADDR32(size)       (CMD_WRITE_ADDR_32 | LBP_SPACE_HM2 | ((size) & 0x7F))
#define CMD_WRITE_HOSTMOT2_ADDR32_INC(size)   (CMD_WRITE_ADDR_32 | LBP_SPACE_HM2 | LBP_ADDR_AUTO_INC | ((size) & 0x7F))

#define LO_BYTE(cmd) ((cmd) & 0xFF)
#define HI_BYTE(cmd) (((cmd) & 0xFF00) >> 8)

// common packets
#define CMD_READ_HM2_COOKIE  (CMD_READ_HOSTMOT2_ADDR32(1))
#define CMD_READ_FLASH_IDROM (CMD_READ_FPGA_FLASH_ADDR32(1))

typedef struct {
    u8 cmd_hi;
    u8 cmd_lo;
} lbp16_cmd;

typedef struct {
    u8 cmd_hi;
    u8 cmd_lo;
    u8 addr_hi;
    u8 addr_lo;
} lbp16_cmd_addr;

typedef struct {
    u8 cmd_hi;
    u8 cmd_lo;
    u8 addr_hi;
    u8 addr_lo;
    u8 data_hi;
    u8 data_lo;
} lbp16_cmd_addr_data16;

typedef struct {
    u8 cmd_hi;
    u8 cmd_lo;
    u8 addr_hi;
    u8 addr_lo;
    u8 data1;
    u8 data2;
    u8 data3;
    u8 data4;
} lbp16_cmd_addr_data32;

typedef struct {
    u8 cmd_hi;
    u8 cmd_lo;
    u8 addr_hi;
    u8 addr_lo;
    u8 page[256];
} lbp16_write_flash_page_packet;

typedef struct {
    lbp16_cmd_addr_data16 write_ena_pck;
    lbp16_cmd_addr_data32 fl_erase_pck;
} lbp16_erase_flash_sector_packets;

typedef struct {
    lbp16_cmd_addr_data16 write_ena_pck;
    lbp16_write_flash_page_packet fl_write_page_pck;
} lbp16_write_flash_page_packets;

typedef struct {
    lbp16_cmd_addr_data16 write_ena_pck;
    lbp16_cmd_addr_data32 eth_write_ip_pck;
} lbp16_write_ip_addr_packets;

#define BOARD_NAME_LEN 16

#define LBP16_INIT_PACKET4(packet, cmd, addr) do { \
    packet.cmd_hi = LO_BYTE(cmd); \
    packet.cmd_lo = HI_BYTE(cmd); \
    packet.addr_hi = LO_BYTE(addr); \
    packet.addr_lo = HI_BYTE(addr); \
    } while (0);

#define LBP16_INIT_PACKET6(packet, cmd, addr, data) do { \
    packet.cmd_hi = LO_BYTE(cmd); \
    packet.cmd_lo = HI_BYTE(cmd); \
    packet.addr_hi = LO_BYTE(addr); \
    packet.addr_lo = HI_BYTE(addr); \
    packet.data_hi = LO_BYTE(data); \
    packet.data_lo = HI_BYTE(data); \
    } while (0);

#define LBP16_INIT_PACKET8(packet, cmd, addr, data) do { \
    packet.cmd_hi = LO_BYTE(cmd); \
    packet.cmd_lo = HI_BYTE(cmd); \
    packet.addr_hi = LO_BYTE(addr); \
    packet.addr_lo = HI_BYTE(addr); \
    packet.data1 = LO_BYTE(data); \
    packet.data2 = HI_BYTE(data); \
    packet.data3 = LO_BYTE((data >> 16) & 0xFFFF); \
    packet.data4 = HI_BYTE((data >> 16) & 0xFFFF); \
    } while (0);

typedef struct {
    u16 cookie;
    u16 size;
    u16 range;
    u16 addr;
    u8  name[8];
} lbp_mem_info_area;

typedef struct {
    u16 reserved1;
    u16 mac_addr_lo;
    u16 mac_addr_mid;
    u16 mac_addr_hi;
    u16 reserved2;
    u16 reserved3;
    u16 reserved4;
    u16 reserved5;
    u8 name[BOARD_NAME_LEN];
    u16 ip_addr_lo;
    u16 ip_addr_hi;
    u16 reserved6;
    u16 reserved7;
    u16 led_debug;
    u16 reserved8;
    u16 reserved9;
    u16 reserved10;
} lbp_eth_eeprom_area;

typedef struct {
    u16 ErrorReg;
    u16 LBPParseErrors;
    u16 LBPMemErrors;
    u16 LBPWriteErrors;
    u16 RXPacketCount;
    u16 RXUDPCount;
    u16 RXBadCount;
    u16 TXPacketCount;
    u16 TXUDPCount;
    u16 TXBadCount;
    u16 led_mode;
    u16 reserved1;
    u16 UDPPktTimeStamp;
} lbp_status_area;

typedef struct {
    u8 name[BOARD_NAME_LEN];
    u16 LBP_version;
    u16 firmware_version;
    u16 jumpers;
} lbp_info_area;

u32 lbp16_send_read_u16(u16 cmd, u16 addr);
void lbp16_send_write_u16(u16 cmd, u16 addr, u16 val);
u32 lbp16_send_read_u32(u16 cmd, u16 addr);
void lbp16_send_sector_erase(u32 addr);
void lbp16_send_flash_address(u32 addr);
void lbp16_send_flash_page_read(void *buff);
void lbp16_send_flash_page_write(void *buff);
void lbp16_send_write_ip_address(char *ip_addr);

#endif
