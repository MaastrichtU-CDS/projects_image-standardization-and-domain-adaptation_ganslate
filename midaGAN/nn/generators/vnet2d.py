import torch
import torch.nn as nn
import memcnn
from midaGAN.nn.utils import get_norm_layer_2d, is_bias_before_norm

# Config imports
from dataclasses import dataclass, field
from omegaconf import MISSING
from midaGAN.conf.config import BaseGeneratorConfig


@dataclass
class Vnet2DConfig(BaseGeneratorConfig):
    """Partially-invertible V-Net generator."""
    name:              str = "Vnet2D"
    use_memory_saving:    bool = True  # Turn on memory saving for invertible layers. [Default: True]
    use_inverse:          bool = True  # Specifies if the inverse forward will be used so that it construct the required layers
    first_layer_channels: int = 16

class Vnet2D(nn.Module):
        def __init__(self, in_channels, norm_type, first_layer_channels=16, use_memory_saving=True, use_inverse=True):
        super().__init__()
        if first_layer_channels % in_channels:
            raise ValueError("`first_layer_channels` has to be divisible by `in_channels`.")

        keep_input = not use_memory_saving
        norm_layer = get_norm_layer_2d(norm_type)
        use_bias = is_bias_before_norm(norm_type)
        self.use_inverse = use_inverse
        is_inplace = not use_inverse  # activations in invertible blocks are not inplace when invertibility is used
        out_channels = in_channels
        
        self.in_ab = InputBlock(in_channels, first_layer_channels, norm_layer, use_bias) 
        if use_inverse:
            self.in_ba = InputBlock(in_channels, first_layer_channels, norm_layer, use_bias)

        self.down1 = DownBlock(first_layer_channels, 1, 
                               norm_layer, use_bias, keep_input, use_inverse, is_inplace)
        self.down2 = DownBlock(first_layer_channels*2, 2, 
                               norm_layer, use_bias, keep_input, use_inverse, is_inplace) 
        self.down3 = DownBlock(first_layer_channels*4, 3, 
                               norm_layer, use_bias, keep_input, use_inverse, is_inplace) 
        self.down4 = DownBlock(first_layer_channels*8, 2, 
                               norm_layer, use_bias, keep_input, use_inverse, is_inplace)

        self.up4 = UpBlock(first_layer_channels*16, first_layer_channels*16, 2, 
                           norm_layer, use_bias, keep_input, use_inverse, is_inplace) 
        self.up3 = UpBlock(first_layer_channels*16, first_layer_channels*8, 2, 
                           norm_layer, use_bias, keep_input, use_inverse, is_inplace) 
        self.up2 = UpBlock(first_layer_channels*8, first_layer_channels*4, 1, 
                           norm_layer, use_bias, keep_input, use_inverse, is_inplace) 
        self.up1 = UpBlock(first_layer_channels*4, first_layer_channels*2, 1, 
                           norm_layer, use_bias, keep_input, use_inverse, is_inplace) 
        
        self.out_ab = OutBlock(first_layer_channels*2, out_channels, norm_layer, use_bias)  
        if use_inverse:
            self.out_ba = OutBlock(first_layer_channels*2, out_channels, norm_layer, use_bias) 

    def forward(self, x, inverse=False):
        if inverse:
            if not self.use_inverse:
                raise ValueError("Trying to perform inverse forward while `use_inverse` flag is turned off.")
            in_block  = self.in_ba
            out_block = self.out_ba
        else:
            in_block  = self.in_ab
            out_block = self.out_ab
        
        out1 = in_block(x)
        out2 = self.down1(out1, inverse)
        out3 = self.down2(out2, inverse)
        out4 = self.down3(out3, inverse)
        out = self.down4(out4, inverse)
        out = self.up4(out, out4, inverse)
        out = self.up3(out, out3, inverse)
        out = self.up2(out, out2, inverse)
        out = self.up1(out, out1, inverse)
        out = out_block(out)
        return out

class InvertibleBlock(nn.Module):
    # TODO: is it possible to pass in a constructed block and make it invertible? The class could be reusable for other architectures
    def __init__(self, n_channels, norm_layer, use_bias, keep_input, is_inplace):
        super().__init__()
        
        invertible_module = memcnn.AdditiveCoupling(
            Fm=self.build_conv_block(n_channels//2, norm_layer, use_bias, is_inplace),
            Gm=self.build_conv_block(n_channels//2, norm_layer, use_bias, is_inplace)
        )
        self.invertible_block = memcnn.InvertibleModuleWrapper(fn=invertible_module, 
                                                               keep_input=keep_input, 
                                                               keep_input_inverse=keep_input)

    def build_conv_block(self, n_channels, norm_layer, use_bias, is_inplace):
        return nn.Sequential(nn.Conv2d(n_channels, n_channels, kernel_size=5, padding=2, bias=use_bias),
                             norm_layer(n_channels),
                             nn.PReLU(n_channels, is_inplace))

    def forward(self, x, inverse=False):
        if inverse:
            return self.invertible_block.inverse(x)
        else:
            return self.invertible_block(x)


class InvertibleSequence(nn.Module):
    def __init__(self, n_channels, n_blocks, norm_layer, use_bias, keep_input, is_inplace):
        super().__init__()

        sequence = []
        for _ in range(n_blocks):
            block = InvertibleBlock(n_channels, norm_layer, use_bias, keep_input, is_inplace)
            sequence.append(block) 
        self.sequence = nn.Sequential(*sequence)
    
    def forward(self, x, inverse=False):
        if inverse:
            sequence = reversed(self.sequence)
        else:
            sequence = self.sequence
        
        for i, block in enumerate(sequence):
            if i == 0:    #https://github.com/silvandeleemput/memcnn/issues/39#issuecomment-599199122
                if inverse:
                    block.invertible_block.keep_input_inverse = True
                else:
                    block.invertible_block.keep_input = True
            x = block(x, inverse=inverse)
        return x        


class InputBlock(nn.Module):
    def __init__(self, in_channels, out_channels, norm_layer, use_bias):
        super().__init__()
        self.n_repeats = out_channels // in_channels  # how many times an image has to be repeated to match `out_channels`
        self.conv1 = nn.Conv2d(in_channels, out_channels, kernel_size=5, padding=2, bias=use_bias)
        self.bn1 = norm_layer(out_channels)
        self.relu = nn.PReLU(out_channels)

    def forward(self, x):
        out = self.bn1(self.conv1(x))
        x_repeated = x.repeat(1, self.n_repeats, 1, 1) # match channel dimension for residual connection
        out = out + x_repeated
        return self.relu(out)


class DownBlock(nn.Module):
    def __init__(self, in_channels, n_conv_blocks, norm_layer, use_bias, keep_input, use_inverse, is_inplace):
        super().__init__()

        out_channels = 2*in_channels
        self.down_conv_ab = self.build_down_conv(in_channels, out_channels, norm_layer, use_bias)
        if use_inverse:
            self.down_conv_ba = self.build_down_conv(in_channels, out_channels, norm_layer, use_bias)
        self.core = InvertibleSequence(out_channels, n_conv_blocks, norm_layer, use_bias, keep_input, is_inplace)
        self.relu = nn.PReLU(out_channels)

    def build_down_conv(self, in_channels, out_channels, norm_layer, use_bias):
        return nn.Sequential(nn.Conv2d(in_channels, out_channels, kernel_size=2, stride=2, bias=use_bias),
                             norm_layer(out_channels),
                             nn.PReLU(out_channels))

    def forward(self, x, inverse=False):
        if inverse:
            down_conv = self.down_conv_ba
        else:
            down_conv = self.down_conv_ab
        down = down_conv(x)
        out = self.core(down, inverse)
        out = out + down
        return self.relu(out)


class UpBlock(nn.Module):
    def __init__(self, in_channels, out_channels, n_conv_blocks, norm_layer, use_bias, keep_input, use_inverse, is_inplace):
        super().__init__()

        self.up_conv_ab = self.build_up_conv(in_channels, out_channels, norm_layer, use_bias)
        if use_inverse:
            self.up_conv_ba = self.build_up_conv(in_channels, out_channels, norm_layer, use_bias)

        self.core = InvertibleSequence(out_channels, n_conv_blocks, norm_layer, use_bias, keep_input, is_inplace)
        self.relu = nn.PReLU(out_channels)
    
    def build_up_conv(self, in_channels, out_channels, norm_layer, use_bias):
        return nn.Sequential(nn.ConvTranspose2d(in_channels, out_channels // 2, 
                                                kernel_size=2, stride=2, bias=use_bias),
                             norm_layer(out_channels // 2),
                             nn.PReLU(out_channels // 2))

    def forward(self, x, skipx, inverse=False):
        if inverse:
            up_conv = self.up_conv_ba
        else:
            up_conv = self.up_conv_ab
        up = up_conv(x)
        xcat = torch.cat((up, skipx), 1)
        out = self.core(xcat, inverse)
        out = out + xcat
        return self.relu(out)


class OutBlock(nn.Module):
    def __init__(self, in_channels, out_channels, norm_layer, use_bias):
        super().__init__()
        self.conv1 = nn.Conv2d(in_channels, in_channels, kernel_size=5, padding=2, bias=use_bias)
        self.bn1 = norm_layer(in_channels)
        self.relu1 = nn.PReLU(in_channels)
        self.conv2 = nn.Conv2d(in_channels, out_channels, kernel_size=1)
        self.tanh = nn.Tanh() 

    def forward(self, x):
        out = self.relu1(self.bn1(self.conv1(x)))
        out = self.conv2(out)
        res = self.tanh(out)
        return res
