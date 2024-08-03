"""
 Copyright (c) 2024, Eeum, Inc.

 This software is licensed under the terms of the Revised BSD License.
 See LICENSE for details.
"""

# pylint: disable=unused-import
from asyncio import gather, create_task, Event, sleep
import glob
from io import BytesIO
from typing import cast
import shutil

import json
import os
import torch
from pathlib import Path

from PIL import Image
from torch import nn
from torch.utils.data import DataLoader
from torchvision import transforms, datasets
from torchvision.models import efficientnet_v2_s, EfficientNet_V2_S_Weights
from torchinfo import summary
from tqdm import tqdm

from opencxl.util.logger import logger
from opencxl.util.number import split_int
from opencxl.cxl.device.cxl_type1_device import CxlType1Device, CxlType1DeviceConfig
from opencxl.cxl.device.cxl_type2_device import (
    CxlType2Device,
    CxlType2DeviceConfig,
)
from opencxl.cxl.component.irq_manager import Irq, IrqManager
from opencxl.cxl.component.switch_connection_client import SwitchConnectionClient
from opencxl.cxl.component.common import CXL_COMPONENT_TYPE
from opencxl.util.component import RunnableComponent


class MyType1Accelerator(RunnableComponent):
    """
    This demo app uses CXL.cache to read metadata from the host memory, uses the
    metadata to train an image classification model, then finally uses CXL.cache
    to rewrite the training results to the host memory.

    The demo app also supports host "validation": the device can copy an image
    from some predefined address in host memory, run the trained model on the
    retrieved image, and write the class probabilities back to the host memory.
    """

    def __init__(
        self,
        port_index: int,
        host: str = "0.0.0.0",
        port: int = 8000,
        server_port: int = 9050,
        device_id: int = 0,
        host_mem_size: int = 0,
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
                device_id=device_id,
                host_mem_size=host_mem_size,
            )
        )
        self.original_base_folder = "/Users/zhxq/Downloads/imagenette2-160"
        self.accel_dirname = f"/tmp/T1Accel@{self._label}"
        if os.path.exists(self.accel_dirname) and os.path.isdir(self.accel_dirname):
            shutil.rmtree(self.accel_dirname)
        Path(self.accel_dirname).mkdir(parents=True, exist_ok=True)
        self._train_folder = f"{self.accel_dirname}{os.path.sep}train"
        self._val_folder = f"{self.accel_dirname}{os.path.sep}val"
        os.symlink(
            src=f"{self.original_base_folder}{os.path.sep}train",
            dst=self._train_folder,
            target_is_directory=True,
        )
        os.symlink(
            src=f"{self.original_base_folder}{os.path.sep}val",
            dst=self._val_folder,
            target_is_directory=True,
        )
        # Model setup
        self.model = efficientnet_v2_s(weights=EfficientNet_V2_S_Weights.DEFAULT)

        # Reset the classification head and freeze params
        self.model.classifier[1] = nn.Linear(in_features=1280, out_features=10, bias=True)
        for p in self.model.features.parameters():
            p.requires_grad = False
        summary(self.model, input_size=(1, 3, 160, 160))

        self.transform = transforms.Compose(
            [
                transforms.Resize((160, 160)),
                transforms.ToTensor(),
            ]
        )

        self._train_dataset = datasets.ImageFolder(
            root=self._train_folder, transform=self.transform
        )
        self._train_dataloader = DataLoader(
            self._train_dataset, batch_size=32, shuffle=True, num_workers=8
        )

        self._test_dataset = datasets.ImageFolder(root=self._val_folder, transform=self.transform)
        self._test_dataloader = DataLoader(
            self._train_dataset, batch_size=10, shuffle=True, num_workers=8
        )

        self._irq_manager = IrqManager(
            addr="127.0.0.1", port=server_port, device_name=label, device_id=device_id
        )

        self._stop_signal = Event()

        self._irq_manager.register_interrupt_handler(Irq.HOST_READY, self._run_app)
        self._irq_manager.register_interrupt_handler(Irq.HOST_SENT, self._validate_model)

    def _train_one_epoch(
        self,
        train_dataloader: DataLoader,
        test_dataloader: DataLoader,
        device: torch.device,
        optimizer,
        loss_fn,
    ):
        # pylint: disable=unused-variable
        self.model.train()
        correct_count = 0
        running_train_loss = 0
        inputs: torch.Tensor
        labels: torch.Tensor
        for _, (inputs, labels) in tqdm(
            enumerate(train_dataloader),
            total=len(train_dataloader),
            desc="Progress",
        ):
            inputs = inputs.to(device)
            labels = labels.to(device)

            # logits
            pred_logits = self.model(inputs)
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

        self.model.eval()
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

                pred_logit = self.model(inputs)
                loss = loss_fn(pred_logit, labels)
                running_test_loss += loss.item() * inputs.size(0)

                pred_classes = torch.argmax(pred_logit, dim=1)
                is_correct = pred_classes == labels

                correct_count += is_correct.sum()

                # logits to probs, placeholder
                pred_probs = torch.softmax(pred_logit, dim=1)

        test_loss = running_test_loss / len(test_dataloader.sampler)
        test_accuracy = correct_count / len(test_dataloader.sampler)

        print(f"test_loss: {test_loss}, test_accuracy: {test_accuracy}")

    async def _get_metadata(self):
        # When retrieving the metadata, the device does not know ahead of time where
        # the metadata is located, nor the size of the metadata. The host relays this
        # information by writing to hardcoded HPAs. Once the accelerator receives the
        # HOST_READY interrupt, it will read the address and size of the metadata from
        # the host memory using CXL.cache, then use CXL.cache again to appropriately
        # request the data from the host, one cacheline at a time.

        # CACHELINE_LENGTH = 64

        metadata_addr_mmio_addr = 0x1800
        metadata_size_mmio_addr = 0x1808
        metadata_addr = await self._cxl_type1_device.read_mmio(metadata_addr_mmio_addr, 8)
        metadata_size = await self._cxl_type1_device.read_mmio(metadata_size_mmio_addr, 8)

        metadata_end = metadata_addr + metadata_size

        print("Writing metadata")
        with open(f"{self.accel_dirname}{os.path.sep}noisy_imagenette.csv", "wb") as md_file:
            print(f"addr: 0x{metadata_addr:x}")
            print(f"end: 0x{metadata_end:x}")
            data = await self._cxl_type1_device.cxl_cache_read(metadata_addr, metadata_size)
            md_file.write(data)

        print("Finished writing file")

    async def _get_test_image(self) -> Image.Image:

        CACHELINE_LENGTH = 64

        image_addr_mmio_addr = 0x1810
        image_size_mmio_addr = 0x1818
        image_addr = await self._cxl_type1_device.read_mmio(image_addr_mmio_addr, 8)
        image_size = await self._cxl_type1_device.read_mmio(image_size_mmio_addr, 8)

        image_end = image_addr + image_size

        im = None

        imgbuf = BytesIO()
        cacheline = await self._cxl_type1_device.cxl_cache_read(image_addr, image_end)
        imgbuf.write(cacheline)

        im = Image.open(imgbuf).convert("RGB")

        return im

    async def _validate_model(self, reader_id):
        # pylint: disable=E1101
        im = await self._get_test_image()
        tens = cast(torch.Tensor, self.transform(im))

        # Model expects a 4-dimensional tensor
        tens = torch.unsqueeze(tens, 0)

        pred_logit = self.model(tens)
        predicted_probs = torch.softmax(pred_logit, dim=1)[0]

        categories = glob.glob(f"{self._val_folder}{os.path.sep}*")
        pred_kv = {
            self._test_dataset.classes[i]: predicted_probs[i].item() for i in range(len(categories))
        }

        json_asenc = str.encode(json.dumps(pred_kv))
        bytes_size = len(json_asenc)

        json_asint = int.from_bytes(json_asenc, "little")

        RESULTS_HPA = 0x900  # Arbitrarily chosen

        rounded_bytes_size = (((bytes_size - 1) // 64) + 1) * 64
        await self._cxl_type1_device.cxl_cache_write(
            RESULTS_HPA, max(64, rounded_bytes_size), json_asint
        )

        HOST_VECTOR_ADDR = 0x1820
        HOST_VECTOR_SIZE = 0x1828

        await self._cxl_type1_device.write_mmio(HOST_VECTOR_ADDR, 8, RESULTS_HPA)
        await self._cxl_type1_device.write_mmio(HOST_VECTOR_SIZE, 8, bytes_size)

        while True:
            json_addr_rb = await self._cxl_type1_device.read_mmio(HOST_VECTOR_ADDR, 8)
            json_size_rb = await self._cxl_type1_device.read_mmio(HOST_VECTOR_SIZE, 8)

            if json_addr_rb == RESULTS_HPA and json_size_rb == bytes_size:
                break
            await sleep(0.2)

        # Done with eval
        await self._irq_manager.send_irq_request(Irq.ACCEL_VALIDATION_FINISHED)

    async def _run_app(self, _):
        print("app running")

        logger.info(self._create_message("Beginning training"))
        if torch.cuda.is_available():
            device = torch.device("cuda:0")
        # elif torch.backends.mps.is_available():
        #     device = torch.device("mps")
        else:
            device = torch.device("cpu")
        print(f"Using torch.device: {device}")

        # Uses CXL.cache to copy metadata from host cached memory into device local memory
        # await self._get_metadata()
        # If testing:
        shutil.copy(
            f"{self.original_base_folder}{os.path.sep}noisy_imagenette.csv",
            f"{self.accel_dirname}{os.path.sep}noisy_imagenette.csv",
        )

        # epochs = 1
        # for epoch in range(epochs):
        #     logger.debug(f"Starting epoch: {epoch}")
        #     loss_fn = torch.nn.CrossEntropyLoss()
        #     optimizer = torch.optim.SGD(self.model.parameters())
        #     scheduler = torch.optim.lr_scheduler.LinearLR(
        #         optimizer, start_factor=1, end_factor=0.5, total_iters=30
        #     )
        #     self._train_one_epoch(
        #         train_dataloader=self._train_dataloader,
        #         test_dataloader=self._test_dataloader,
        #         optimizer=optimizer,
        #         loss_fn=loss_fn,
        #         device=device,
        #     )
        #     scheduler.step()
        #     logger.debug(f"Epoch: {epoch} finished")

        # Done training
        print("Training Done!!!")
        await self._irq_manager.send_irq_request(Irq.ACCEL_TRAINING_FINISHED)

    async def _app_shutdown(self):
        logger.info("Removing accelerator directory")
        shutil.rmtree(self.accel_dirname)

    async def _run(self):
        tasks = [
            create_task(self._sw_conn_client.run()),
            create_task(self._cxl_type1_device.run()),
            create_task(self._irq_manager.run()),
            create_task(self._stop_signal.wait()),
        ]
        await self._sw_conn_client.wait_for_ready()
        await self._cxl_type1_device.wait_for_ready()
        await self._irq_manager.wait_for_ready()
        await self._irq_manager.start_connection()
        await self._change_status_to_running()
        await gather(*tasks)

    async def _stop(self):
        print("!!!!!Device trying to stop")
        self._stop_signal.set()
        tasks = [
            create_task(self._sw_conn_client.stop()),
            create_task(self._cxl_type1_device.stop()),
            create_task(self._irq_manager.stop()),
        ]
        await gather(*tasks)
        await self._app_shutdown()
        print("!!!!!Device stopped!!")


class MyType2Accelerator(RunnableComponent):
    """
    This demo app demonstrates the host's ability to write metadata to the
    device cached memory via CXL.mem; after the device uses this metadata to
    train an image classification model, the host can use CXL.mem to read
    the training results from the device memory.

    The demo app also supports host "validation": the host can copy an image into
    some predefined address in device memory, send an interrupt to the device to
    start training, and read the class probabilities after training concludes.
    """

    # pylint: disable=unused-argument
    def __init__(
        self,
        port_index: int,
        memory_size: int,
        memory_file: str,
        host: str = "0.0.0.0",
        port: int = 8000,
        server_port: int = 9050,
    ):
        label = f"Port{port_index}"
#         super().__init__(label)
#         self._sw_conn_client = SwitchConnectionClient(
#             port_index, CXL_COMPONENT_TYPE.T2, host=host, port=port
#         )

#         device_config = CxlType2DeviceConfig(
#             device_name=label,
#             transport_connection=self._sw_conn_client.get_cxl_connection(),
#             memory_size=memory_size,
#             memory_file=memory_file,
#         )
#         self._cxl_type2_device = CxlType2Device(device_config)
#         self.accel_dirname = f"/Users/zhxq/Downloads/imagenette2-160/T2Accel@{self._label}"

#         # Don't run the following code for now
#         # pylint: disable=unreachable
#         return

#         # Model setup
#         self.model = efficientnet_v2_s(weights=EfficientNet_V2_S_Weights.DEFAULT)

#         # Reset the classification head and freeze params
#         self.model.classifier[1] = nn.Linear(in_features=1280, out_features=10, bias=True)
#         for p in self.model.features.parameters():
#             p.requires_grad = False
#         summary(self.model, input_size=(1, 3, 160, 160))

#         self.transform = transforms.Compose(
#             [
#                 transforms.Resize((160, 160)),
#                 transforms.ToTensor(),
#             ]
#         )

#         self._train_dataset = datasets.ImageFolder(root="train", transform=self.transform)
#         self._train_dataloader = DataLoader(
#             self._train_dataset, batch_size=32, shuffle=True, num_workers=4
#         )

#         self._test_dataset = datasets.ImageFolder(root="val", transform=self.transform)
#         self._test_dataloader = DataLoader(
#             self._train_dataset, batch_size=10, shuffle=True, num_workers=4
#         )

#         self._irq_manager = IrqManager(
#             server_bind_port=irq_listen_port,
#             client_target_port=[irq_send_port],
#             device_name=label,
#         )

#         # self._irq_manager.register_interrupt_handler(Irq.HOST_READY, self._run_app)
#         # self._irq_manager.register_interrupt_handler(Irq.HOST_SENT, self._validate_model)

#     def _train_one_epoch(self, train_dataloader, test_dataloader, device, optimizer, loss_fn):
#         # pylint: disable=unused-variable
#         self.model.train()
#         correct_count = 0
#         running_train_loss = 0
#         for _, (inputs, labels) in tqdm(
#             enumerate(train_dataloader),
#             total=len(train_dataloader),
#             desc="Progress",
#         ):
#             inputs = inputs.to(device)
#             labels = labels.to(device)

#             # logits
#             pred_logits = self.model(inputs)
#             loss = loss_fn(pred_logits, labels)
#             predicted_prob = torch.softmax(pred_logits, dim=1)
#             pred_classes = torch.argmax(predicted_prob, dim=1)

#             loss.backward()

#             optimizer.step()

#             is_correct = pred_classes == labels
#             correct_count += is_correct.sum()
#             running_train_loss += loss.item() * inputs.size(0)

#         train_loss = running_train_loss / len(train_dataloader.sampler)
#         train_accuracy = correct_count / len(train_dataloader.sampler)
#         print(f"train_loss: {train_loss}, train_accuracy: {train_accuracy}")

#         if device == "cuda:0":
#             torch.cuda.empty_cache()

#         self.model.eval()
#         with torch.no_grad():
#             running_test_loss = 0
#             correct_count = 0
#             for _, (inputs, labels) in tqdm(
#                 enumerate(test_dataloader),
#                 total=len(test_dataloader),
#                 desc="Progress",
#             ):
#                 inputs = inputs.to(device)
#                 labels = labels.to(device)

#                 pred_logit = self.model(inputs)
#                 loss = loss_fn(pred_logit, labels)
#                 running_test_loss += loss.item() * inputs.size(0)

#                 pred_classes = torch.argmax(pred_logit, dim=1)
#                 is_correct = pred_classes == labels

#                 correct_count += is_correct.sum()

#                 # logits to probs, placeholder
#                 pred_probs = torch.softmax(pred_logit, dim=1)

#         test_loss = running_test_loss / len(test_dataloader.sampler)
#         test_accuracy = correct_count / len(test_dataloader.sampler)

#         print(f"test_loss: {test_loss}, test_accuracy: {test_accuracy}")

#     async def _get_metadata(self):
#         # When downloading the metadata, the device does not know ahead of time where
#         # the metadata is located, nor the size of the metadata. The host relays this
#         # information by writing to hardcoded DPAs using CXL.mem. Once the accelerator
#         # receives the HOST_READY interrupt, it will read the address and size of the
#         # metadata from its own memory, then use CXL.cache to appropriately request
#         # the data from the host, one cacheline at a time.

#         METADATA_ADDR_DPA = 0x40
#         METADATA_SIZE_DPA = 0x48

#         CACHELINE_LENGTH = 64

#         metadata_addr = await self._cxl_type2_device.read_mem_dpa(METADATA_ADDR_DPA, 8)
#         metadata_size = await self._cxl_type2_device.read_mem_dpa(METADATA_SIZE_DPA, 8)

#         with open("noisy_imagenette.csv", "wb") as md_file:
#             for cacheline_offset in range(metadata_addr, metadata_size, CACHELINE_LENGTH):
#                 cacheline = await self._cxl_type2_device.cxl_cache_readline(cacheline_offset)
#                 cacheline = cast(int, cacheline)
#                 md_file.write(cacheline.to_bytes(CACHELINE_LENGTH))

#     async def _get_test_image(self) -> Image.Image:
#         IMAGE_ADDR_DPA = 0x40
#         IMAGE_SIZE_DPA = 0x48

#         CACHELINE_LENGTH = 64

#         image_addr = await self._cxl_type2_device.read_mem_dpa(IMAGE_ADDR_DPA, 8)
#         image_size = await self._cxl_type2_device.read_mem_dpa(IMAGE_SIZE_DPA, 8)

#         im = None

#         with BytesIO() as imgbuf:
#             for cacheline_offset in range(image_addr, image_size, CACHELINE_LENGTH):
#                 cacheline = await self._cxl_type2_device.cxl_cache_readline(cacheline_offset)
#                 cacheline = cast(int, cacheline)
#                 imgbuf.write(cacheline.to_bytes(CACHELINE_LENGTH))
#             im = Image.open(imgbuf)

#         return im

#     async def _validate_model(self):
#         # pylint: disable=no-member
#         im = await self._get_test_image()
#         tens = cast(torch.Tensor, self.transform(im))

#         # Model expects a 4-dimensional tensor
#         tens = torch.unsqueeze(tens, 0)

#         pred_logit = self.model(tens)
#         predicted_probs = torch.softmax(pred_logit, dim=1)

#         # 10 predicted classes
#         # TODO: avoid magic number usage
#         pred_kv = {self.test_dataset.classes[i]: predicted_probs[i] for i in range(0, 10)}

#         json_asenc = str.encode(json.dumps(pred_kv))
#         bytes_size = len(json_asenc)

#         json_asint = int.from_bytes(json_asenc)

#         RESULTS_DPA = 0x180  # Arbitrarily chosen
#         await self._cxl_type2_device.write_mem_dpa(RESULTS_DPA, json_asint, bytes_size)

#         HOST_VECTOR_ADDR = 0x50
#         HOST_VECTOR_SIZE = 0x58
#         await self._cxl_type2_device.write_mem_dpa(HOST_VECTOR_ADDR, RESULTS_DPA, 8)
#         await self._cxl_type2_device.write_mem_dpa(HOST_VECTOR_SIZE, bytes_size, 8)

#         # Done with eval
#         await self._irq_manager.send_irq_request(Irq.ACCEL_VALIDATION_FINISHED)

#     async def _run_app(self):
#         # pylint: disable=unused-variable
#         # pylint: disable=E1101
#         logger.info(
#             self._create_message(f"Changing into accelerator directory: {self.accel_dirname}")
#         )
#         os.chdir(self.accel_dirname)

#         logger.info(self._create_message("Creating symlinks to training and validation datasets"))
#         os.symlink(
#             src="/Users/zhxq/Downloads/imagenette2-160/train", dst="train", target_is_directory=True
#         )
#         os.symlink(
#             src="/Users/zhxq/Downloads/imagenette2-160/val", dst="val", target_is_directory=True
#         )

#         logger.info(self._create_message("Beginning training"))
#         if torch.cuda.is_available():
#             device = torch.device("cuda:0")
#         elif torch.backends.mps.is_available():
#             device = torch.device("mps")
#         else:
#             device = torch.device("cpu")
#         print(f"torch.device: {device}")

#         # Uses CXL.cache to copy metadata from host cached memory into device local memory
#         await self._get_metadata()

#         epochs = 2
#         epoch_loss = 0
#         for epoch in range(epochs):
#             loss_fn = torch.nn.CrossEntropyLoss()
#             optimizer = torch.optim.SGD(self.model.parameters())
#             scheduler = torch.optim.lr_scheduler.LinearLR(
#                 optimizer, start_factor=1, end_factor=0.5, total_iters=30
#             )
#             self._train_one_epoch(
#                 train_dataloader=self._train_dataloader,
#                 test_dataloader=self._test_dataloader,
#                 optimizer=optimizer,
#                 loss_fn=loss_fn,
#                 device=device,
#             )
#             scheduler.step()

#         # Done training
#         await self._irq_manager.send_irq_request(Irq.ACCEL_TRAINING_FINISHED)

#     async def _app_shutdown(self):
#         logger.info("Moving out of accelerator directory")
#         os.chdir("..")

#         # logger.info("Removing accelerator directory")
#         # os.rmdir(self.accel_dirname)

#     async def _run(self):
#         tasks = [
#             create_task(self._sw_conn_client.run()),
#             create_task(self._cxl_type2_device.run()),
#             # create_task(self._irq_manager.run()),
#         ]
#         await self._sw_conn_client.wait_for_ready()
#         await self._cxl_type2_device.wait_for_ready()
#         # await self._irq_manager.wait_for_ready()
#         await self._change_status_to_running()
#         await gather(*tasks)

#     async def _stop(self):
#         tasks = [
#             create_task(self._sw_conn_client.stop()),
#             create_task(self._cxl_type2_device.stop()),
#             # create_task(self._irq_manager.stop()),
#         ]
#         await gather(*tasks)
#         # await self._app_shutdown()
