.set noreorder
# delay slot of "jal MEMPACK_Init" in main: ramSize 0x200000 -> 0x700000
# (levels get 7MB; 0x80700000+ holds the track table, the stack moved to 0x807FFFF0)
lui $a0, 0x70
