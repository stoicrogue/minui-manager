import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatChipsModule } from '@angular/material/chips';
import { MatButtonModule } from '@angular/material/button';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';
import { MatDialog, MatDialogModule } from '@angular/material/dialog';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { HttpErrorResponse } from '@angular/common/http';

import { SDCardGame, SDCardListing, SDCardService } from '../../services/sdcard.service';
import { RemoveGameDialogComponent } from './remove-game-dialog.component';

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
    MatDialogModule,
    MatSnackBarModule,
  ],
  templateUrl: './games.component.html',
  styleUrl: './games.component.scss',
})
export class GamesComponent implements OnInit {
  private readonly api = inject(SDCardService);
  private readonly dialog = inject(MatDialog);
  private readonly snack = inject(MatSnackBar);

  readonly listing = signal<SDCardListing | null>(null);
  readonly loading = signal<boolean>(false);
  readonly notReady = signal<NotReadyDetail | null>(null);
  readonly genericError = signal<string | null>(null);
  /** game_folder_name currently being imported, or null. Disables its button. */
  readonly importing = signal<string | null>(null);
  /** Bumped on every refresh so box-art <img src> URLs get a fresh
   * query string — otherwise re-syncs serve the previously cached PNG. */
  readonly listingTick = signal<number>(Date.now());

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
        this.listingTick.set(Date.now());
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
    return this.api.boxArtUrl(game.game_folder_name, this.listingTick());
  }

  trackByFolder(_index: number, game: SDCardGame): string {
    return game.game_folder_name;
  }

  openImportToLibrary(game: SDCardGame): void {
    if (this.importing()) return;
    this.importing.set(game.game_folder_name);
    this.api.importToLibrary(game.game_folder_name).subscribe({
      next: () => {
        this.importing.set(null);
        this.snack.open(
          `Imported ${game.display_name} to the library.`,
          undefined,
          { duration: 4000 },
        );
        this.refresh(); // refresh so matches_library_id flips and the button greys out
      },
      error: (err: HttpErrorResponse) => {
        this.importing.set(null);
        const detail = err.error?.detail ?? err.message ?? 'Import failed.';
        this.snack.open(`Couldn't import ${game.display_name}: ${detail}`, 'Dismiss', {
          duration: 6000,
        });
      },
    });
  }

  openRemove(game: SDCardGame): void {
    const ref = this.dialog.open(RemoveGameDialogComponent, {
      data: { game },
      maxWidth: '90vw',
      autoFocus: false,
    });
    ref.afterClosed().subscribe((result) => {
      if (result?.removed) {
        this.snack.open(
          `Archived ${game.display_name}. You can restore it from the Library page.`,
          undefined,
          { duration: 4000 },
        );
        this.refresh();
      }
    });
  }
}
