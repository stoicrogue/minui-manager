import { Component, inject, signal } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import {
  MAT_DIALOG_DATA,
  MatDialogModule,
  MatDialogRef,
} from '@angular/material/dialog';
import { MatButtonModule } from '@angular/material/button';
import { MatIconModule } from '@angular/material/icon';
import { MatProgressSpinnerModule } from '@angular/material/progress-spinner';
import { MatChipsModule } from '@angular/material/chips';

import { SDCardGame, SDCardService } from '../../services/sdcard.service';

export interface RemoveGameData {
  game: SDCardGame;
}

@Component({
  selector: 'app-remove-game-dialog',
  standalone: true,
  imports: [
    CommonModule,
    MatDialogModule,
    MatButtonModule,
    MatIconModule,
    MatProgressSpinnerModule,
    MatChipsModule,
  ],
  templateUrl: './remove-game-dialog.component.html',
  styleUrl: './remove-game-dialog.component.scss',
})
export class RemoveGameDialogComponent {
  private readonly api = inject(SDCardService);
  private readonly dialogRef = inject(MatDialogRef<RemoveGameDialogComponent>);
  readonly data = inject<RemoveGameData>(MAT_DIALOG_DATA);

  readonly working = signal<boolean>(false);
  readonly errorMessage = signal<string | null>(null);

  confirm(): void {
    this.working.set(true);
    this.errorMessage.set(null);
    this.api.removeGame(this.data.game.game_folder_name).subscribe({
      next: (r) => {
        this.dialogRef.close({ removed: true, archived: r.archived });
      },
      error: (err: HttpErrorResponse) => {
        this.working.set(false);
        this.errorMessage.set(
          err.error?.detail ?? err.message ?? 'Could not archive this game.',
        );
      },
    });
  }

  cancel(): void {
    this.dialogRef.close({ removed: false });
  }
}
