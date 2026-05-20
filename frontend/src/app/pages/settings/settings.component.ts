import { Component, OnInit, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatChipsModule } from '@angular/material/chips';
import { MatSnackBar, MatSnackBarModule } from '@angular/material/snack-bar';
import { MatDividerModule } from '@angular/material/divider';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatTooltipModule } from '@angular/material/tooltip';

import {
  AppSettings,
  SDCardStatus,
  SDCardStatusResponse,
  SettingsService,
} from '../../services/settings.service';

@Component({
  selector: 'app-settings-page',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatButtonModule,
    MatIconModule,
    MatChipsModule,
    MatSnackBarModule,
    MatDividerModule,
    MatProgressSpinnerModule,
    MatTooltipModule,
  ],
  templateUrl: './settings.component.html',
  styleUrl: './settings.component.scss',
})
export class SettingsComponent implements OnInit {
  private readonly api = inject(SettingsService);
  private readonly snack = inject(MatSnackBar);

  readonly settings = signal<AppSettings | null>(null);
  readonly status = signal<SDCardStatusResponse | null>(null);
  readonly sdPathInput = signal<string>('');
  readonly slotCapInput = signal<number | null>(10);
  readonly loading = signal<boolean>(false);
  readonly saving = signal<boolean>(false);
  readonly picking = signal<boolean>(false);

  ngOnInit(): void {
    this.refresh();
  }

  refresh(): void {
    this.loading.set(true);
    this.api.getSettings().subscribe({
      next: (s) => {
        this.settings.set(s);
        this.sdPathInput.set(s.sd_card_path ?? '');
        this.slotCapInput.set(s.max_games_total);
        this.loadStatus();
      },
      error: (err) => {
        this.loading.set(false);
        this.snack.open(`Failed to load settings: ${err.message}`, 'Dismiss', { duration: 5000 });
      },
    });
  }

  private loadStatus(): void {
    this.api.getSDCardStatus().subscribe({
      next: (s) => {
        this.status.set(s);
        this.loading.set(false);
      },
      error: (err) => {
        this.loading.set(false);
        this.snack.open(`Failed to fetch SD status: ${err.message}`, 'Dismiss', { duration: 5000 });
      },
    });
  }

  saveSDPath(): void {
    const path = this.sdPathInput().trim();
    this.saving.set(true);
    this.api.updateSettings({ sd_card_path: path === '' ? null : path }).subscribe({
      next: (s) => {
        this.settings.set(s);
        this.loadStatus();
        this.saving.set(false);
        this.snack.open('SD card path saved.', undefined, { duration: 2000 });
      },
      error: (err) => {
        this.saving.set(false);
        this.snack.open(`Save failed: ${err.message}`, 'Dismiss', { duration: 5000 });
      },
    });
  }

  browseSDPath(): void {
    this.picking.set(true);
    this.api.pickSDCardFolder().subscribe({
      next: ({ path }) => {
        this.picking.set(false);
        if (path) {
          this.sdPathInput.set(path);
          // Auto-save after a successful pick — the user just told us which
          // folder they want, so verify immediately rather than making them
          // click Save next.
          this.saveSDPath();
        }
        // path === null means the user cancelled — leave the input alone.
      },
      error: (err) => {
        this.picking.set(false);
        this.snack.open(`Could not open folder picker: ${err.message}`, 'Dismiss', {
          duration: 5000,
        });
      },
    });
  }

  saveSlotCap(): void {
    const value = this.slotCapInput();
    this.saving.set(true);
    this.api.updateSettings({ max_games_total: value }).subscribe({
      next: (s) => {
        this.settings.set(s);
        this.saving.set(false);
        this.snack.open('Slot cap saved.', undefined, { duration: 2000 });
      },
      error: (err) => {
        this.saving.set(false);
        this.snack.open(`Save failed: ${err.message}`, 'Dismiss', { duration: 5000 });
      },
    });
  }

  statusColor(s: SDCardStatus | undefined): 'primary' | 'accent' | 'warn' | undefined {
    switch (s) {
      case 'ok':
        return 'primary';
      case 'invalid':
      case 'not_found':
        return 'warn';
      case 'not_set':
        return 'accent';
      default:
        return undefined;
    }
  }

  statusIcon(s: SDCardStatus | undefined): string {
    switch (s) {
      case 'ok':
        return 'check_circle';
      case 'invalid':
        return 'error';
      case 'not_found':
        return 'help';
      case 'not_set':
        return 'sd_card';
      default:
        return 'sd_card';
    }
  }

  statusLabel(s: SDCardStatus | undefined): string {
    switch (s) {
      case 'ok':
        return 'OK';
      case 'invalid':
        return 'Invalid';
      case 'not_found':
        return 'Not found';
      case 'not_set':
        return 'Not set';
      default:
        return 'Unknown';
    }
  }
}
