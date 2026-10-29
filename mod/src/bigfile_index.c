#include <common.h>
#include "multitrack.h"

// Retail signature (the decomp drops fileType and adds it at the call site).
// Must stay within the original 0x10C bytes.
int LOAD_GetBigfileIndex(unsigned int levelID, int lod, int fileType)
{
	int index;
	struct GameTracker* gGT = sdata->gGT;

	if (levelID <= LAB_BASEMENT)
	{
		index = BI_ARCADETRACKS + levelID * 8;

		// custom track picked in the track list; never leak it into
		// adventure or cups, which reuse the host slot
		if
		(
			(MT_STATE->magic == MT_MAGIC) &&
			(MT_STATE->redirectLevel == (int)levelID) &&
			((gGT->gameMode1 & (ADVENTURE_MODE | ADVENTURE_ARENA)) == 0) &&
			((gGT->gameMode2 & CUP_ANY_KIND) == 0)
		)
		{
			index = MT_STATE->redirectBase;
		}

		return index + sdata->levBigLodIndex[lod - 1] + fileType;
	}

	if (levelID <= CITADEL_CITY)
		index = BI_ADVENTUREHUB + (levelID - GEM_STONE_VALLEY) * 3;
	else if (levelID <= INTRO_OXIDE)
		index = BI_CUTSCENES_INTRO + (levelID - INTRO_RACE_TODAY) * 3;
	else if (levelID <= ADVENTURE_GARAGE)
		index = BI_MAINMENUFILE + (levelID - MAIN_MENU_LEVEL) * 2;
	else if (levelID == NAUGHTY_DOG_CRATE)
		index = BI_NDBOX;
	else if (levelID <= OXIDE_TRUE_ENDING)
		index = BI_CUTSCENES_OUTRO + (levelID - OXIDE_ENDING) * 2;
	else if (levelID <= CREDITS_MEGUMI)
		index = BI_CREDITS + (levelID - CREDITS_CRASH) * 3;
	else
		index = BI_SCRAPBOOK;

	return index + fileType;
}
