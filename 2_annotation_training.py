import os

import numpy as np
import tifffile

import torch
import torch.nn as nn
import torchvision
import torch.nn.functional as F

assert torch.cuda.is_available(), 'No GPU available'

num_classes = 4 # Including 0, the "unannotated" class
assert num_classes < 2**8 # Bro you don't need more
model = torchvision.models.segmentation.fcn_resnet50(
    pretrained=False,
    num_classes=num_classes - 1, # Only guess the annotated classes
    ).cuda()

optimizer = torch.optim.Adam(model.parameters(), lr=1e-3, amsgrad=True)

saved_state_filename = './saved_model.pt'
backup_saved_state_filename = './backup_saved_model.pt'
starting_epoch = 0
if os.path.exists(saved_state_filename):
    print("Loading saved model and optimizer state.")
    checkpoint = torch.load(saved_state_filename)
    model.load_state_dict(checkpoint['model_state_dict'])
    optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
    starting_epoch = 1 + checkpoint['epoch']
    model.train()

def loss_fn(output, target, num_annotated_pixels):
    # Our target has an extra slice at the beginning for unannotated pixels:
    output, target = output['out'], target[:, 1:, :, :]
    assert output.shape == target.shape
    probs = F.softmax(output, dim=1)
    return torch.sum(probs * target / -num_annotated_pixels)

img_dir = './2_human_annotations'

def load_data(img_name):
    assert len(os.listdir(img_dir))>0, 'no human-annotated images found'
    img_path = os.path.join(img_dir, img_name)
    img = tifffile.imread(img_path)
    assert img.shape[0] == 2 and len(img.shape) == 3 # 2-channel, 2D images
    # First channel holds the raw image. Match shape and dtype to what
    # torch expects: (batch_size, 3, y, x) and float32
    input_ = torch.cuda.FloatTensor(
        img[np.newaxis, 0:1, ...].astype('float32'))
    input_ = input_.repeat(1, 3, 1, 1) # Repeat grayscale 3x to get RGB
    input_.requires_grad = True
    # Second channel holds our annotations of the raw image. Annotation
    # values are ints ranging from 0 to num_classes; each different
    # annotation value signals a different class. We unpack these into a
    # "1-hot" representation called 'target'.
    assert 0 <= img[1, :, :].min() and img[1, :, :].max() < num_classes
    num_annotated_pixels = np.count_nonzero(img[1, :, :])
    # Might as well pass a small dtype to the GPU, but the on-GPU dtype
    # has to be Long to work with .scatter_:
    labels = torch.cuda.LongTensor(
        img[np.newaxis, 1:2, :, :].astype('uint8'))
    # An empty Boolean array to be filled with our "1-hot" representation:
    target = torch.cuda.BoolTensor(
        1, num_classes, img.shape[1], img.shape[2]
        ).zero_() # Initialization to zero is not automatic!
    # Copy each class into its own boolean image:
    target.scatter_(dim=1, index=labels.data, value=True)
    return input_, target, num_annotated_pixels

def save_output(output, filename, dir_='./3_machine_annotations'):
    guess = F.softmax(output['out'].cpu().data, dim=1).numpy().astype('float32')
    tifffile.imwrite(
        os.path.join(dir_, filename),
        guess,
        photometric='MINISBLACK',
        imagej=True,
        ijmetadata={'Ranges:', (0, 1)*guess.shape[1]})    

for epoch in range(starting_epoch, 100000): # Basically forever
    img_names = [i for i in os.listdir(img_dir) if i.endswith('.tif')]
    loss_list = []
    print("\nEpoch", epoch)
    for i, img_name in enumerate(img_names):
        print('.', sep='', end='')
        input_, target, num_annotated_pixels = load_data(img_name)
        output = model(input_)
        loss = loss_fn(output, target, num_annotated_pixels)
        loss.backward()
        optimizer.step()
        optimizer.zero_grad()
        # Outputs for inspection and debugging:
        loss_list.append(loss.detach().item())
        save_output(output, img_name)
        if i == 0:
            if not os.path.isdir('./convergence'): os.mkdir('./convergence')
            save_output(output, 'e%06i_'%(epoch)+img_name, dir_='./convergence')
    print('\nLosses:')
    print(''.join('%0.5f '%x for x in loss_list))
    if os.path.exists(saved_state_filename):
        os.replace(saved_state_filename, backup_saved_state_filename)
    torch.save(
        {'epoch': epoch,
         'model_state_dict': model.state_dict(),
         'optimizer_state_dict': optimizer.state_dict()},
        saved_state_filename)



