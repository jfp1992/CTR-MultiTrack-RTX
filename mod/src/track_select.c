#include <common.h>
#include "multitrack.h"

// retail 230 functions the headers only declare with a DECOMP_ prefix
unsigned char MM_TransitionInOut(struct TransitionMeta* meta, int framesPassed, int numFrames);
void MM_Characters_RestoreIDs(void);
void MM_Battle_Init(void);
void MM_TrackSelect_Video_State(int state);
char MM_TrackSelect_boolTrackOpen(struct MainMenu_LevelRow* menuSelect);
int RECTMENU_ProcessInput(struct RectMenu* menu);

#define ROWS_VISIBLE 10
#define ROW_HEIGHT   0xE
#define LIST_X       0x24
#define LIST_Y       0x26
#define LIST_W       0x1B8

// The toolchain places this file's code at MM_TrackSelect_MenuProc's address
// in source order, so the hooked function must come first; helpers go below.
static int MT_RowInfo(struct MainMenu_LevelRow* stock, int numStock, int battle, int row, int* levID, struct MTEntry** custom);
static int MT_NoBots(struct GameTracker* gGT, struct MTEntry* e);

void MM_TrackSelect_MenuProc(struct RectMenu* menu)
{
	struct GameTracker* gGT = sdata->gGT;
	short elapsedFrames = D230.trackSel_transitionFrames;
	int battle = (gGT->gameMode1 & BATTLE_MODE) != 0;

	if (MT_STATE->magic != MT_MAGIC)
	{
		MT_STATE->magic = MT_MAGIC;
		MT_STATE->redirectLevel = -1;
		MT_STATE->cursor[0] = 0;
		MT_STATE->cursor[1] = 0;
	}

	if (D230.trackSel_transitionState == ENTERING_MENU)
	{
		sdata->errorMessagePosIndex = battle ? 2 : 1;
		MM_TransitionInOut(&D230.transitionMeta_trackSel[0], elapsedFrames, FPS_DOUBLE(8));
		if (elapsedFrames == 0) D230.trackSel_transitionState = IN_MENU;
		else elapsedFrames--;
	}
	else if (D230.trackSel_transitionState == EXITING_MENU)
	{
		MM_TransitionInOut(&D230.transitionMeta_trackSel[0], elapsedFrames, FPS_DOUBLE(8));
		elapsedFrames++;

		if (elapsedFrames > FPS_DOUBLE(12))
		{
			sdata->errorMessagePosIndex = 0;

			if (D230.trackSel_StartRaceAfterFadeOut == 0)
			{
				MT_STATE->redirectLevel = -1;
				sdata->ptrDesiredMenu = &D230.menuCharacterSelect;
				MM_Characters_RestoreIDs();
				return;
			}

			if (battle)
			{
				sdata->ptrDesiredMenu = &D230.menuBattleWeapons;
				MM_Battle_Init();
				return;
			}

			if ((gGT->gameMode1 & TIME_TRIAL) != 0)
			{
				sdata->ptrGhostTapePlaying = MEMPACK_AllocHighMem(0x3e00);
				memset(sdata->ptrGhostTapePlaying, 0, 0x28);
				sdata->boolReplayHumanGhost = 0;
				SelectProfile_ToggleMode(0x30);
				sdata->ptrDesiredMenu = &data.menuGhostSelection;
				return;
			}

			sdata->ptrDesiredMenu = &data.menuQueueLoadTrack;
			return;
		}
	}
	D230.trackSel_transitionFrames = elapsedFrames;

	struct MainMenu_LevelRow* stock = battle ? &D230.battleTracks[0] : &D230.arcadeTracks[0];
	int numStock = battle ? 7 : 18;

	int levID = 0;
	struct MTEntry* custom;
	int numRows = MT_RowInfo(stock, numStock, battle, -1, &levID, &custom);
	int cursor = MT_STATE->cursor[battle];
	if (cursor >= numRows) cursor = 0;

	struct MTEntry* cursorEntry;
	MT_RowInfo(stock, numStock, battle, cursor, &levID, &cursorEntry);

	if (D230.trackSel_boolOpenLapBox == 0)
	{
		int tap = sdata->buttonTapPerPlayer[0] &
			(BTN_UP | BTN_DOWN | BTN_L1 | BTN_R1 | BTN_TRIANGLE | BTN_SQUARE_one | BTN_CROSS_one | BTN_CIRCLE);

		if ((D230.trackSel_transitionState == IN_MENU) && (tap != 0))
		{
			if (tap & BTN_UP)        cursor = (cursor == 0) ? numRows - 1 : cursor - 1;
			else if (tap & BTN_DOWN) cursor = (cursor + 1 >= numRows) ? 0 : cursor + 1;
			else if (tap & BTN_L1)   cursor = (cursor < ROWS_VISIBLE) ? 0 : cursor - ROWS_VISIBLE;
			else if (tap & BTN_R1)   cursor = (cursor + ROWS_VISIBLE >= numRows) ? numRows - 1 : cursor + ROWS_VISIBLE;

			if (tap & (BTN_UP | BTN_DOWN | BTN_L1 | BTN_R1)) OtherFX_Play(0, 1);

			if ((tap & (BTN_CROSS_one | BTN_CIRCLE)) && MT_NoBots(gGT, cursorEntry))
			{
				OtherFX_Play(2, 1);
			}
			else if (tap & (BTN_CROSS_one | BTN_CIRCLE))
			{
				OtherFX_Play(1, 1);
				if ((gGT->gameMode1 & (BATTLE_MODE | TIME_TRIAL)) == 0)
				{
					D230.trackSel_boolOpenLapBox = D230.trackSel_transitionState;
				}
				else
				{
					D230.trackSel_StartRaceAfterFadeOut = D230.trackSel_transitionState;
					D230.trackSel_transitionState = EXITING_MENU;
				}
			}
			else if (tap & (BTN_TRIANGLE | BTN_SQUARE_one))
			{
				OtherFX_Play(2, 1);
				D230.trackSel_StartRaceAfterFadeOut = 0;
				D230.trackSel_transitionState = EXITING_MENU;
			}

			RECTMENU_ClearInput();
		}
	}
	else
	{
		short lapState = 0;
		D230.menuLapSel.rowSelected = sdata->uselessLapRowCopy;

		if (D230.trackSel_transitionState == IN_MENU)
			lapState = RECTMENU_ProcessInput(&D230.menuLapSel);

		RECTMENU_DrawSelf(&D230.menuLapSel,
			D230.transitionMeta_trackSel[2].currX, D230.transitionMeta_trackSel[2].currY, 0xa4);

		sdata->uselessLapRowCopy = D230.menuLapSel.rowSelected;
		gGT->numLaps = D230.lapRowVal[D230.menuLapSel.rowSelected];

		if (lapState == 1)
		{
			D230.trackSel_transitionState = EXITING_MENU;
			D230.trackSel_StartRaceAfterFadeOut = 1;
		}
		else if (lapState == -1)
		{
			D230.trackSel_boolOpenLapBox = 0;
		}

		if ((gGT->gameMode2 & CHEAT_ONELAP) != 0) gGT->numLaps = 1;
	}

	MT_STATE->cursor[battle] = cursor;

	// highlighted row decides what QueueLoadTrack will load
	MT_RowInfo(stock, numStock, battle, cursor, &levID, &custom);
	gGT->currLEV = levID;
	MT_STATE->redirectBase = custom ? custom->bigBase : 0;
	MT_STATE->redirectLevel = custom ? custom->slot : -1;

	// no track preview video: custom tracks have none, and it would show the host track's
	MM_TrackSelect_Video_State(1);

	int x = D230.transitionMeta_trackSel[0].currX;
	int y = D230.transitionMeta_trackSel[0].currY;
	int top = cursor - ROWS_VISIBLE / 2;
	if (top > numRows - ROWS_VISIBLE) top = numRows - ROWS_VISIBLE;
	if (top < 0) top = 0;

	char header[32];
	sprintf(header, "%d/%d  L1/R1 PAGE", cursor + 1, numRows);
	DecalFont_DrawLine(header, x + LIST_X + LIST_W - 8, y + LIST_Y - 0xE, FONT_SMALL, (JUSTIFY_RIGHT | PERIWINKLE));
	DecalFont_DrawLine(battle ? "SELECT ARENA" : "SELECT TRACK", x + LIST_X + 8, y + LIST_Y - 0xE, FONT_SMALL, ORANGE);

	for (int i = 0; (i < ROWS_VISIBLE) && (top + i < numRows); i++)
	{
		int row = top + i;
		int rowLev;
		struct MTEntry* e;
		MT_RowInfo(stock, numStock, battle, row, &rowLev, &e);

		char* name = e ? e->name : sdata->lngStrings[data.metaDataLEV[rowLev].name_LNG];
		int color = (row == cursor) ? ORANGE : (e ? WHITE : PERIWINKLE);
		DecalFont_DrawLine(name, x + LIST_X + 8, y + LIST_Y + 4 + i * ROW_HEIGHT, FONT_SMALL, color);

		if (row == cursor)
		{
			RECT q = { x + LIST_X + 2, y + LIST_Y + 1 + i * ROW_HEIGHT, LIST_W - 4, ROW_HEIGHT - 1 };
			CTR_Box_DrawClearBox(&q, &sdata->menuRowHighlight_Normal, TRANS_50_DECAL,
				gGT->backBuffer->otMem.startPlusFour, &gGT->backBuffer->primMem);
		}
	}

	if (MT_NoBots(gGT, custom))
	{
		DecalFont_DrawLine("NO AI PATHS - USE TIME TRIAL OR VS", x + LIST_X + LIST_W / 2,
			y + LIST_Y + ROWS_VISIBLE * ROW_HEIGHT + 0x16, FONT_SMALL, (JUSTIFY_CENTER | RED));
	}

	if (custom)
	{
		char info[64];
		sprintf(info, "BY %s  -  ON %s", custom->author,
			sdata->lngStrings[data.metaDataLEV[custom->slot].name_LNG]);
		DecalFont_DrawLine(info, x + LIST_X + LIST_W / 2, y + LIST_Y + ROWS_VISIBLE * ROW_HEIGHT + 0xA,
			FONT_SMALL, (JUSTIFY_CENTER | WHITE));
	}

	RECT r = { x + LIST_X, y + LIST_Y, LIST_W, ROWS_VISIBLE * ROW_HEIGHT + 4 };
	RECTMENU_DrawInnerRect(&r, 0, gGT->backBuffer->otMem.startPlusFour);
}

// Rows are the unlocked stock tracks of this mode, then every custom track
// whose host slot belongs to this mode. Returns the number of rows.
static int MT_RowInfo(struct MainMenu_LevelRow* stock, int numStock, int battle, int row, int* levID, struct MTEntry** custom)
{
	int n = 0;
	*custom = 0;

	for (int i = 0; i < numStock; i++)
	{
		if (!MM_TrackSelect_boolTrackOpen(&stock[i])) continue;
		if (n == row) *levID = stock[i].levID;
		n++;
	}

	int count = (MT_TABLE->magic == MT_MAGIC) ? MT_TABLE->count : 0;
	struct MTEntry* e = MT_TABLE->entries;
	for (int i = 0; i < count; i++, e++)
	{
		if (((e->flags & MT_FLAG_BATTLE) != 0) != battle) continue;
		if (n == row) { *levID = e->slot; *custom = e; }
		n++;
	}
	return n;
}

// arcade fills the grid with bots, which need the lev's nav paths
static int MT_NoBots(struct GameTracker* gGT, struct MTEntry* e)
{
	return e && (e->flags & MT_FLAG_NO_AI) && (gGT->gameMode1 & ARCADE_MODE);
}
