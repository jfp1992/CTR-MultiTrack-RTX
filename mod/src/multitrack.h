#ifndef MULTITRACK_H
#define MULTITRACK_H

#include <common.h>

/*
 * Custom tracks are appended to BIGFILE.BIG by scripts/pack_tracks.py:
 * each track owns 8 entries laid out like a stock level slot
 * ({1P,2P,4P,relic} x {vrm,lev}). The track table (MTHeader + MTEntry[count],
 * src/track_table.bin) is compiled onto the end of overlay 230, so the menu
 * reads it from RAM and never touches the CD. Selecting a custom track makes
 * LOAD_GetBigfileIndex redirect its host slot (the level it was built over)
 * to those entries, so every race/battle mode runs on the unmodified engine.
 */

#define MT_MAGIC 0x4B52544D // 'MTRK'

// 8MB expansion RAM above the 7MB MEMPACK (patches/mempack7mb.s);
// the stack starts at 0x807FFFF0 and grows down towards it
#define MT_STATE ((volatile struct MTState*) 0x80700000)

// free space between the end of overlay 230 and RB_EndOfFile (largest overlay
// of the 230-233 region), where buildList.txt places src/track_table.bin
extern char MM_EndOfFile[];
#define MT_TABLE ((struct MTHeader*) MM_EndOfFile)

#define MT_NAME_LEN   28
#define MT_AUTHOR_LEN 16

enum MTFlags
{
	MT_FLAG_BATTLE = 1,
	MT_FLAG_NO_AI = 2,  // lev has no nav paths: arcade bots would have nowhere to drive
	MT_FLAG_REMASTER = 4, // Nitro-Fueled track: the stock level, drawn by the native ray tracer from assets/rtx_remaster
};

struct MTEntry
{
	u_char slot;     // host levelID
	u_char flags;    // MTFlags
	u_short bigBase; // first of this track's 8 bigfile entries
	char name[MT_NAME_LEN];
	char author[MT_AUTHOR_LEN];
};

struct MTHeader
{
	int magic;
	int count;
	struct MTEntry entries[0];
};

struct MTState
{
	int magic;          // MT_MAGIC once the menu has initialised this struct
	int redirectLevel;  // host levelID to redirect, -1 = none
	int redirectBase;   // bigfile index replacing levelID*8
	short cursor[2];    // list cursor for race / battle lists
};

#endif
