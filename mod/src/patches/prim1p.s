.set noreorder
# MainInit_PrimMem 1P: primMem_SizePerLEV_1P[lev] << 10 -> << 13 (8x polygon budget);
# custom tracks overflow the stock budget and drop polygons depending on the camera
sll $s0, $a2, 13
