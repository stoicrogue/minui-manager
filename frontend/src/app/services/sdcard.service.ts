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

@Injectable({ providedIn: 'root' })
export class SDCardService {
  private readonly http = inject(HttpClient);

  getGames(): Observable<SDCardListing> {
    return this.http.get<SDCardListing>('/api/sdcard/games');
  }

  getOrphanArt(): Observable<{ art: OrphanArt[] }> {
    return this.http.get<{ art: OrphanArt[] }>('/api/sdcard/orphan-art');
  }

  /** Build a URL the browser can put in an <img src>. */
  boxArtUrl(gameFolderName: string): string {
    return `/api/sdcard/box-art?name=${encodeURIComponent(gameFolderName)}`;
  }
}
