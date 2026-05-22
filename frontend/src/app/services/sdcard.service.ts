import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export interface SDCardGame {
  system_code: string;
  game_folder_name: string;
  display_name: string;
  folder_path: string;
  rom_filename: string | null;
  rom_path: string | null;
  m3u_path: string | null;
  has_rom_file: boolean;
  has_boxart: boolean;
  boxart_path: string | null;
  has_save: boolean;
  save_path: string | null;
  is_malformed: boolean;
  malformed_reason: string | null;
  matches_library_id: number | null;
  disc_filenames: string[];
  is_multi_disk: boolean;
}

export interface SDCardListing {
  games: SDCardGame[];
  slot_count: number;
  slot_cap: number | null;
  summary: {
    total: number;
    with_boxart: number;
    with_save: number;
    malformed: number;
  };
}

export interface OrphanArt {
  filename: string;
  game_folder_name: string;
  system_code: string | null;
  path: string;
}

export type SyncOpAction = 'mkdir' | 'copy' | 'write_text' | 'remove_tree';

export interface SyncOp {
  action: SyncOpAction;
  dest_rel: string;
  src: string | null;
  size_bytes: number | null;
  note: string | null;
}

export interface SyncPlanGame {
  library_id: number;
  game_folder_name: string;
  system_code: string;
  display_name: string;
  rom_filename: string;
  is_replacement: boolean;
  has_boxart: boolean;
  boxart_missing_reason: string | null;
  ops: SyncOp[];
}

export interface SyncPlan {
  games: SyncPlanGame[];
  new_slot_count: number;
  current_slot_count: number;
  slot_cap: number | null;
  total_ops: number;
}

export interface SyncGameResult {
  library_id: number;
  game_folder_name: string;
  status: 'ok' | 'error';
  files_written: number;
  bytes_written: number;
  skipped_boxart: boolean;
  error: string | null;
}

export interface SyncResultPayload {
  started_at: string;
  completed_at: string;
  games: SyncGameResult[];
  ok_count: number;
  error_count: number;
}

export interface SyncResponse {
  dry_run: boolean;
  plan: SyncPlan;
  result?: SyncResultPayload;
}

export interface SlotCapConflict {
  code: 'slot_cap_exceeded';
  cap: number;
  current_slot_count: number;
  projected_slot_count: number;
  current_games: SDCardGame[];
  new_folder_names: string[];
  replacing_folder_names: string[];
}

@Injectable({ providedIn: 'root' })
export class SDCardService {
  private readonly http = inject(HttpClient);

  getGames(): Observable<SDCardListing> {
    return this.http.get<SDCardListing>('/api/sdcard/games');
  }

  getOrphanArt(): Observable<{ art: OrphanArt[] }> {
    return this.http.get<{ art: OrphanArt[] }>('/api/sdcard/orphan-art');
  }

  /** Build a URL the browser can put in an <img src>. The optional
   * version is appended as a cache-buster so re-syncs (which write new
   * bytes to the same URL) don't render the previously cached image. */
  boxArtUrl(gameFolderName: string, version?: string | number): string {
    const v = version ?? Date.now();
    return `/api/sdcard/box-art?name=${encodeURIComponent(gameFolderName)}&v=${v}`;
  }

  /** Plan (or execute) a sync of the given library entries. */
  sync(libraryIds: number[], dryRun: boolean): Observable<SyncResponse> {
    return this.http.post<SyncResponse>(
      `/api/sdcard/sync?dry_run=${dryRun}`,
      { library_ids: libraryIds },
    );
  }

  /** Archive a game off the SD card. */
  removeGame(gameFolderName: string): Observable<{ archived: ArchivedGame }> {
    return this.http.delete<{ archived: ArchivedGame }>(
      `/api/sdcard/games/${encodeURIComponent(gameFolderName)}`,
    );
  }

  /** Copy a game from the SD card into the laptop library. */
  importToLibrary(gameFolderName: string): Observable<{ imported: ImportedLibraryGame }> {
    return this.http.post<{ imported: ImportedLibraryGame }>(
      `/api/sdcard/games/${encodeURIComponent(gameFolderName)}/import-to-library`,
      {},
    );
  }
}

export interface ImportedLibraryGame {
  id: number;
  system_code: string;
  rom_filename: string;
  display_name: string;
  game_folder_name: string;
  size_bytes: number;
  added_at: string;
  library_path: string;
  has_boxart: boolean;
  boxart_path: string | null;
}

export interface ArchivedGame {
  id: number;
  system_code: string;
  game_folder_name: string;
  display_name: string;
  rom_filename: string;
  archive_path: string;
  archive_relpath: string;
  has_save: boolean;
  has_boxart: boolean;
  archived_at: string;
}
