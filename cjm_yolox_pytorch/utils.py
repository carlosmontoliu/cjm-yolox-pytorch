# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/01_utils.ipynb.

# %% auto 0
__all__ = ['multi_apply', 'download_file']

# %% ../nbs/01_utils.ipynb 4
from pathlib import Path

from typing import Any, Type, List, Optional, Callable, Tuple
from functools import partial

import urllib.request

# %% ../nbs/01_utils.ipynb 6
def multi_apply(func:Callable[..., Any], # Function to apply.
                *args:Any,
                **kwargs:Any
               ) -> Tuple[List[Any], ...]:
    """
    Applies the function `func` to each set of arguments in `*args`, 
    possibly using keyword arguments `**kwargs`.
    
    Based on OpenMMLab's implementation in the mmdetection library:
    
    - [OpenMMLab's Implementation](https://github.com/open-mmlab/mmdetection/blob/d64e719172335fa3d7a757a2a3636bd19e9efb62/mmdet/core/utils/misc.py#L11)

    """
    try:
        pfunc = partial(func, **kwargs) if kwargs else func
        map_results = map(pfunc, *args)
        return tuple(map(list, zip(*map_results)))
    except Exception as e:
        print(f"Error occurred: {str(e)}")
        return ()

# %% ../nbs/01_utils.ipynb 9
def download_file(url:str, # The URL of the file to download. It should be a string containing a valid URL.
                  destination:str, # The path where the file will be saved. This should include the filename and extension as well.
                  overwrite:bool=False # Determines if the file should be overwritten if it already exists at the destination.
                 ) -> None:
    
    """
    Downloads a file from a given URL and saves it to a specific destination. The function creates the necessary directories in the destination path if they do not exist.
    """
    
    # Check if the destination file already exists and if we are allowed to overwrite it
    if not Path(destination).exists() or overwrite:

        # Create parent directories if they do not exist
        Path(destination).parent.mkdir(parents=True, exist_ok=True)

        try:
            # Attempt to download the file from the URL and save it to the destination
            urllib.request.urlretrieve(url, destination)

            # Print a success message if the download is successful
            print("Download complete!")

        except Exception as e:
            # Print an error message if the download fails
            print(f"An error occurred while downloading the file: {e}")