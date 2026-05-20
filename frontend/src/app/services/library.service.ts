import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

export type DetectionConfidence = 'high' | 'medium' | 'low' | 'unknown';

export interface SystemCandidate {
  code: string;
  display_name: string;
}

export interface SystemDetection {
  detected_code: string | null;
  confidence: DetectionConfidence;
  candidates: SystemCandidate[];
  suggested_display_name: string;
  reason: string;
}

export interface UploadResponse {
  draft_id: string;
  original_filename: string;
  size_bytes: number;
  detection: SystemDetection;
}

export interface LibraryGame {
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

export interface LibraryListing {
  games: LibraryGame[];
  total: number;
}

@Injectable({ providedIn: 'root' })
export class LibraryService {
  private readonly http = inject(HttpClient);

  upload(file: File): Observable<UploadResponse> {
    const form = new FormData();
    form.append('file', file);
    return this.http.post<UploadResponse>('/api/library/upload', form);
  }

  confirmDraft(
    draftId: string,
    systemCode: string,
    displayName: string,
  ): Observable<LibraryGame> {
    return this.http.post<LibraryGame>(`/api/library/drafts/${draftId}/confirm`, {
      system_code: systemCode,
      display_name: displayName,
    });
  }

  cancelDraft(draftId: string): Observable<{ removed: boolean }> {
    return this.http.delete<{ removed: boolean }>(`/api/library/drafts/${draftId}`);
  }

  list(systemCode?: string): Observable<LibraryListing> {
    const params: Record<string, string> = {};
    if (systemCode) params['system_code'] = systemCode;
    return this.http.get<LibraryListing>('/api/library', { params });
  }

  remove(libraryId: number): Observable<{ deleted: boolean }> {
    return this.http.delete<{ deleted: boolean }>(`/api/library/${libraryId}`);
  }
}
