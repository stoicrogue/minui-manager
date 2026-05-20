import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatChipsModule } from '@angular/material/chips';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { HttpErrorResponse } from '@angular/common/http';

import { SDCardGame, SDCardListing, SDCardService } from '../../services/sdcard.service';

interface NotReadyDetail {
  code: string;
  status: string;
  message: string;
}

@Component({
  selector: 'app-games-page',
  standalone: true,
  imports: [
    CommonModule,
    RouterLink,
    MatCardModule,
    MatIconModule,
    MatChipsModule,
    MatButtonModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
  ],
  templateUrl: './games.component.html',
  styleUrl: './games.component.scss',
})
export class GamesComponent implements OnInit {
  private readonly api = inject(SDCardService);

  readonly listing = signal<SDCardListing | null>(null);
  readonly loading = signal<boolean>(false);
  readonly notReady = signal<NotReadyDetail | null>(null);
  readonly genericError = signal<string | null>(null);

  readonly slotCountLabel = computed(() => {
    const l = this.listing();
    if (!l) return '';
    return l.slot_cap == null ? `${l.slot_count}` : `${l.slot_count} / ${l.slot_cap}`;
  });

  readonly capacityState = computed<'ok' | 'warn' | 'full'>(() => {
    const l = this.listing();
    if (!l || l.slot_cap == null) return 'ok';
    if (l.slot_count >= l.slot_cap) return 'full';
    if (l.slot_count >= l.slot_cap - 1) return 'warn';
    return 'ok';
  });

  ngOnInit(): void {
    this.refresh();
  }

  refresh(): void {
    this.loading.set(true);
    this.notReady.set(null);
    this.genericError.set(null);
    this.api.getGames().subscribe({
      next: (l) => {
        this.listing.set(l);
        this.loading.set(false);
      },
      error: (err: HttpErrorResponse) => {
        this.loading.set(false);
        if (err.status === 400 && err.error?.detail?.code === 'sd_card_not_ready') {
          this.notReady.set(err.error.detail as NotReadyDetail);
        } else {
          this.genericError.set(err.message ?? String(err));
        }
      },
    });
  }

  boxArt(game: SDCardGame): string {
    return this.api.boxArtUrl(game.game_folder_name);
  }

  trackByFolder(_index: number, game: SDCardGame): string {
    return game.game_folder_name;
  }
}
