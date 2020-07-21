# Organize Photos

Collect your mess of photos and videos into one tidy location organized by year, month and day of creation.

## How to use
Just run the script in Python 3 and pass it a folder of images e.g.

organize-photos.py <path_to_folder>

Or if you have multiple folders all over the place, pick one folder to hold your full collection, pass that to the
script first, then pass all other folders e.g.

organize-photos.py <path_to_collection_folder> [<path_to_other_folders>...]


## What does it do?
Moves and renames photos and videos to organize by year, month and day of creation.

The file creation date is extracted from the following locations;

1] From the filename if the date is in any of the following formats:
```
Year-Month-Day_HourMinuteSecond  e.g. 2020-07-18_140127.jpg    
Year-Month-Day  e.g. 2020-07-18.jpg    
Year-Month  e.g. 2020-07.jpg  
IMG-YearMonthDay  e.g.  IMG-20200718.jpg  
VID_YearMonthDay  e.g.  VID_20200718.mov  
```

2] From the file metadata of the following file types:
```
JPEG:      .jpg, .jpeg  
PNG:       .png 
TIFF:      .tif, .tiff  
AVI:       .avi, .mpg, .mpeg
Quicktime: .mov, .mp4, .m4v
```

3] The file system modification date


The result will be a top-level folder for each year, and subfolders for each month. Within those folders will be the
moved photo and video files, renamed as follows;
```
    <year>-<month>-<day>_<hour><minute><second>_<checksum>.<extension>

e.g. 2020-07-18_140127_a3d7639c3f451ed397cb.jpg
```

enjoy!
 
frankie@rootcode.org
