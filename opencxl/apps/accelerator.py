"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

from asyncio import gather, create_task

import torch
from torch import nn
from torch.utils.data import DataLoader
from torchvision import transforms, datasets
from torchvision.models import efficientnet_v2_s, EfficientNet_V2_S_Weights
from torchinfo import summary
from tqdm import tqdm

from opencxl.util.logger import logger
from opencxl.util.component import RunnableComponent
from opencxl.cxl.device.cxl_type1_device import CxlType1Device, CxlType1DeviceConfig
from opencxl.cxl.device.cxl_type2_device import (
    CxlType2Device,
    CxlType2DeviceConfig,
)
from opencxl.cxl.component.switch_connection_client import SwitchConnectionClient
from opencxl.cxl.component.common import CXL_COMPONENT_TYPE


# Example devices based on type1 and type2 devices


class MyType1Accelerator(RunnableComponent):
    def __init__(
        self,
        port_index: int,
        host: str = "0.0.0.0",
        port: int = 8000,
    ):
        label = f"Port{port_index}"
        super().__init__(label)
        self._sw_conn_client = SwitchConnectionClient(
            port_index, CXL_COMPONENT_TYPE.T1, host=host, port=port
        )
        self._cxl_type1_device = CxlType1Device(
            CxlType1DeviceConfig(
                transport_connection=self._sw_conn_client.get_cxl_connection(),
                device_name=label,
            )
        )

    async def _run_app(self, *args):
        # example app: prints the arguments
        for idx, arg in enumerate(args):
            logger.info(self._create_message(f"Type 1 Accelerator: arg{idx}, {arg}"))

    async def _run(self):
        tasks = [
            create_task(self._sw_conn_client.run()),
            create_task(self._cxl_type1_device.run()),
        ]
        await self._sw_conn_client.wait_for_ready()
        await self._cxl_type1_device.wait_for_ready()
        tasks.append(create_task(self._run_app(1, 2)))
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        tasks = [
            create_task(self._sw_conn_client.stop()),
            create_task(self._cxl_type1_device.stop()),
        ]
        await gather(*tasks)


class MyType2Accelerator(RunnableComponent):
    def __init__(
        self,
        port_index: int,
        memory_size: int,
        memory_file: str,
        host: str = "0.0.0.0",
        port: int = 8000,
    ):
        label = f"Port{port_index}"
        super().__init__(label)
        self._sw_conn_client = SwitchConnectionClient(
            port_index, CXL_COMPONENT_TYPE.T2, host=host, port=port
        )

        device_config = CxlType2DeviceConfig(
            device_name=label,
            transport_connection=self._sw_conn_client.get_cxl_connection(),
            memory_size=memory_size,
            memory_file=memory_file,
        )
        self._cxl_type2_device = CxlType2Device(device_config)

    def _train_one_epoch(
        self,
        model,
        train_dataloader: DataLoader,
        test_dataloader: DataLoader,
        device,
        optimizer,
        loss_fn,
    ):
        # pylint: disable=unused-variable
        model.train()
        correct_count = 0
        running_train_loss = 0
        for _, (inputs, labels) in tqdm(
            enumerate(train_dataloader),
            total=len(train_dataloader),
            desc="Progress",
        ):
            inputs = inputs.to(device)
            labels = labels.to(device)

            # logits
            pred_logits = model(inputs)
            loss = loss_fn(pred_logits, labels)
            predicted_prob = torch.softmax(pred_logits, dim=1)
            pred_classes = torch.argmax(predicted_prob, dim=1)

            loss.backward()

            optimizer.step()

            is_correct = pred_classes == labels
            correct_count += is_correct.sum()
            running_train_loss += loss.item() * inputs.size(0)

        train_loss = running_train_loss / len(train_dataloader.sampler)
        train_accuracy = correct_count / len(train_dataloader.sampler)
        print(f"train_loss: {train_loss}, train_accuracy: {train_accuracy}")

        if device == "cuda:0":
            torch.cuda.empty_cache()

        model.eval()
        with torch.no_grad():
            running_test_loss = 0
            correct_count = 0
            for _, (inputs, labels) in tqdm(
                enumerate(test_dataloader),
                total=len(test_dataloader),
                desc="Progress",
            ):
                inputs = inputs.to(device)
                labels = labels.to(device)

                pred_logit = model(inputs)
                loss = loss_fn(pred_logit, labels)
                running_test_loss += loss.item() * inputs.size(0)

                pred_classes = torch.argmax(pred_logit, dim=1)
                is_correct = pred_classes == labels

                correct_count += is_correct.sum()

                # logits to probs, placeholder
                pred_probs = torch.softmax(pred_logit, dim=1)

                # TODO: WRITE pred_probs to HDM and signal host
                # HERE

        test_loss = running_test_loss / len(test_dataloader.sampler)
        test_accuracy = correct_count / len(test_dataloader.sampler)

        print(f"test_loss: {test_loss}, test_accuracy: {test_accuracy}")

        model = efficientnet_v2_s(weights=EfficientNet_V2_S_Weights.DEFAULT)
        device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        print(f"torch.device: {device}")

        # Reset the classification head and freeze params
        model.classifier[1] = nn.Linear(in_features=1280, out_features=10, bias=True)
        for p in model.features.parameters():
            p.requires_grad = False
        summary(model, input_size=(1, 3, 160, 160))

        my_transform = transforms.Compose(
            [
                transforms.Resize((160, 160)),
                transforms.ToTensor(),
            ]
        )
        train_dataset = datasets.ImageFolder(root="imagenette2-160/train", transform=my_transform)
        train_dataloader = DataLoader(train_dataset, batch_size=32, shuffle=True, num_workers=4)

        test_dataset = datasets.ImageFolder(root="imagenette2-160/val", transform=my_transform)
        test_dataloader = DataLoader(test_dataset, batch_size=10, shuffle=True, num_workers=4)

    async def _run_app(self):
        # pylint: disable=unused-variable
        model = efficientnet_v2_s(weights=EfficientNet_V2_S_Weights.DEFAULT)
        device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        print(f"torch.device: {device}")

        # Reset the classification head and freeze params
        model.classifier[1] = nn.Linear(in_features=1280, out_features=10, bias=True)
        for p in model.features.parameters():
            p.requires_grad = False
        summary(model, input_size=(1, 3, 160, 160))

        my_transform = transforms.Compose(
            [
                transforms.Resize((160, 160)),
                transforms.ToTensor(),
            ]
        )
        train_dataset = datasets.ImageFolder(root="imagenette2-160/train", transform=my_transform)
        train_dataloader = DataLoader(train_dataset, batch_size=32, shuffle=True, num_workers=4)

        test_dataset = datasets.ImageFolder(root="imagenette2-160/val", transform=my_transform)
        test_dataloader = DataLoader(test_dataset, batch_size=10, shuffle=True, num_workers=4)

        epochs = 2
        epoch_loss = 0
        for epoch in range(epochs):
            loss_fn = torch.nn.CrossEntropyLoss()
            optimizer = torch.optim.SGD(model.parameters())
            scheduler = torch.optim.lr_scheduler.LinearLR(
                optimizer, start_factor=1, end_factor=0.5, total_iters=30
            )
            self._train_one_epoch(
                model=model,
                train_dataloader=train_dataloader,
                test_dataloader=test_dataloader,
                optimizer=optimizer,
                loss_fn=loss_fn,
                device=device,
            )
            scheduler.step()

    async def _run(self):
        tasks = [
            create_task(self._sw_conn_client.run()),
            create_task(self._cxl_type2_device.run()),
        ]
        await self._sw_conn_client.wait_for_ready()
        await self._cxl_type2_device.wait_for_ready()
        # tasks.append(create_task(self._run_app(1, 2, 3, 4)))
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        tasks = [
            create_task(self._sw_conn_client.stop()),
            create_task(self._cxl_type2_device.stop()),
        ]
        await gather(*tasks)
