import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { LibraryGame } from './library.service';

export interface BoxartCandidate {
  name: string;
  score: number;
  source_url: string;
  source: 'libretro';
}

export interface BoxartSearchResponse {
  library_id: number;
  query: string;
  system_code: string;
  repo: string | null;
  candidates: BoxartCandidate[];
  cache_hit: boolean;
  note: string | null;
}

@Injectable({ providedIn: 'root' })
export class BoxartService {
  private readonly http = inject(HttpClient);

  search(libraryId: number, queryOverride?: string): Observable<BoxartSearchResponse> {
    const params: Record<string, string | number> = { library_id: libraryId };
    if (queryOverride) params['query'] = queryOverride;
    return this.http.get<BoxartSearchResponse>('/api/boxart/search', { params });
  }

  select(libraryId: number, sourceUrl: string, sourceName?: string): Observable<LibraryGame> {
    return this.http.post<LibraryGame>('/api/boxart/select', {
      library_id: libraryId,
      source_url: sourceUrl,
      source_name: sourceName,
    });
  }

  /** URL the frontend can put into <img src> to render a library entry's
   * selected art. Add a cache-buster so updates show immediately. */
  libraryBoxArtUrl(libraryId: number, version?: string | number): string {
    const v = version ?? Date.now();
    return `/api/library/${libraryId}/box-art?v=${v}`;
  }
}
