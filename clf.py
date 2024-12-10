import pandas as pd
import torch
from torch.utils.data import Dataset, DataLoader, random_split
import pytorch_lightning as pl
from torch import nn
import torch.nn.functional as F
from torchmetrics import F1Score, Precision, Recall
import torchmetrics
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.manifold import TSNE
import numpy as np
from pytorch_lightning.loggers import WandbLogger
import wandb



FOLDER = "data/02-processed/activation_tracker.pkl"
objet_of_study = "mlp_act" # mlp_act or states
layer = 1
seed = 2024
HIDDEN_SIZE = 512
BATCH_SIZE = 8
LR = 1e-3
VAL_SPLIT = 0.1
pl.seed_everything(seed)
WANDB = True

# Paso 1: Crear el Dataset personalizado
class ActivationDataset(Dataset):
    def __init__(self, activations, tasks):
        self.activations = torch.tensor(activations, dtype=torch.float32)
        self.tasks = torch.tensor(tasks, dtype=torch.long)  # asumiendo que las tareas son clases etiquetadas con enteros

    def __len__(self):
        return len(self.activations)

    def __getitem__(self, idx):
        return self.activations[idx], self.tasks[idx]
    

# Paso 2: Crear el DataLoader
def create_dataloaders(activations, tasks, batch_size=BATCH_SIZE, val_split=0.1):
    dataset = ActivationDataset(activations, tasks)
    val_size = int(len(dataset) * val_split)
    train_size = len(dataset) - val_size
    train_dataset, val_dataset = random_split(dataset, [train_size, val_size])
    
    train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True, num_workers=10)
    print(f"Train dataset size: {len(train_dataset)}")
    val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False, num_workers=10)
    print(f"Validation dataset size: {len(val_dataset)}")
    
    return train_loader, val_loader

class MLPClassifier(pl.LightningModule):
    def __init__(self, input_size, num_classes, hidden_size=HIDDEN_SIZE, lr=1e-3):
        super(MLPClassifier, self).__init__()
        self.lr  = lr
        self.fc1 = nn.Linear(input_size, hidden_size)
        self.fc2 = nn.Linear(hidden_size, hidden_size)
        self.fc3 = nn.Linear(hidden_size, num_classes)
        task     = "classification" if num_classes > 2 else "binary"
        
        # Métricas
        self.train_acc = torchmetrics.Accuracy(task=task, num_classes=num_classes)
        self.val_acc   = torchmetrics.Accuracy(task=task, num_classes=num_classes)
        self.test_acc  = torchmetrics.Accuracy(task=task, num_classes=num_classes)

        self.test_f1 = F1Score(num_classes=num_classes, average='macro', task=task)
        self.test_precision = Precision(num_classes=num_classes, average='macro', task=task)
        self.test_recall = Recall(num_classes=num_classes, average='macro', task=task)
        
    def forward(self, x):
        x = F.relu(self.fc1(x))
        x = F.relu(self.fc2(x))
        x = self.fc3(x)
        return x

    # Training Step
    def training_step(self, batch, batch_idx):
        x, y = batch
        logits = self(x)
        loss = F.cross_entropy(logits, y)
        preds = torch.argmax(logits, dim=1)

        # Calcular métricas
        acc = self.train_acc(preds, y)

        # Log de métricas
        self.log('train_loss', loss, on_step=False, on_epoch=True, prog_bar=True)
        self.log('train_acc', acc, on_step=False, on_epoch=True, prog_bar=True)

        return loss

    # Validation Step
    def validation_step(self, batch, batch_idx):
        x, y = batch
        logits = self(x)
        loss = F.cross_entropy(logits, y)
        preds = torch.argmax(logits, dim=1)

        # Calcular métricas
        acc = self.val_acc(preds, y)

        # Log de métricas
        self.log('val_loss', loss, on_step=False, on_epoch=True, prog_bar=True)
        self.log('val_acc', acc, on_step=False, on_epoch=True, prog_bar=True)

        return loss

    # Test Step (usar el mismo conjunto que validación para calcular los resultados)
    def test_step(self, batch, batch_idx):
        x, y = batch
        logits = self(x)
        loss = F.cross_entropy(logits, y)
        preds = torch.argmax(logits, dim=1)

        # Calcular métricas
        acc = self.test_acc(preds, y)
        f1 = self.test_f1(preds, y)
        precision = self.test_precision(preds, y)
        recall = self.test_recall(preds, y)

        # Log de métricas
        self.log('test_loss', loss, on_step=False, on_epoch=True)
        self.log('test_acc', acc, on_step=False, on_epoch=True)
        self.log('test_f1', f1, on_step=False, on_epoch=True)
        self.log('test_precision', precision, on_step=False, on_epoch=True)
        self.log('test_recall', recall, on_step=False, on_epoch=True)

        return loss

    # Optimizer configuration
    def configure_optimizers(self):
        optimizer = torch.optim.Adam(self.parameters(), lr=self.lr)
        return optimizer


if __name__ == "__main__":
    # Load the dataset

    if WANDB:
        wandb_logger = WandbLogger(project="llama-3.2-1B-multilingual-interpretability", name=f"{objet_of_study}_layer_{layer}")
        wandb_logger.log_hyperparams({"hidden_size": HIDDEN_SIZE, "batch_size": BATCH_SIZE, "seed": seed})

    dataset = pd.read_pickle(FOLDER)
    column = f"{objet_of_study}_{layer}"
    activations = np.vstack(dataset[column].tolist())
    tasks = dataset["language"].astype("category").cat.codes.values
    y_task = np.concatenate([np.full(array.shape[0], label) for array, label in zip(dataset[column].tolist(), tasks)])
    names_tasks = dataset["language"].tolist()
    y_task_names = np.concatenate([np.full(array.shape[0], label) for array, label in zip(dataset[column].tolist(), names_tasks)])
    print(f"Activations shape: {activations.shape}")
    print(f"Tasks shape: {y_task.shape}")

    # Paso 3: Crear los dataloaders
    train_loader, val_loader = create_dataloaders(activations, y_task, batch_size=BATCH_SIZE, val_split=VAL_SPLIT)
    print("DataLoaders created")
    model = MLPClassifier(input_size=activations.shape[1], num_classes=len(np.unique(y_task)), hidden_size=HIDDEN_SIZE, lr=LR)
    trainer = pl.Trainer(max_epochs=10, logger=wandb_logger if WANDB else None)
    trainer.fit(model, train_loader, val_loader)
    trainer.test(model, dataloaders=val_loader)

    # tsne of the activations
    tsne = TSNE(n_components=2, random_state=seed)
    activations_tsne = tsne.fit_transform(activations)
    plt.figure(figsize=(10, 10))
    sns.scatterplot(x=activations_tsne[:, 0], y=activations_tsne[:, 1], hue=y_task_names, palette="tab10")
    plt.title(f"TSNE of {objet_of_study} activations for layer {layer}")
    # to wandb
    if WANDB:
        wandb.log({"tsne_plot": wandb.Image(plt)})
        plt.close()
    else:
        plt.show()
    

