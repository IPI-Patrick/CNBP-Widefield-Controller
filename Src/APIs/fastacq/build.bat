@echo off
cd /d "%~dp0"
call "C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
nvcc -O3 -std=c++17 --shared -o fastacq.pyd fastacq.cu ^
 -I"C:\Users\patri\AppData\Local\Programs\Python\Python310\Include" ^
 -I"C:\CodingProjects\Widefield-Controller\venv\lib\site-packages\pybind11\include" ^
 -L"C:\Users\patri\AppData\Local\Programs\Python\Python310\libs" ^
 -lcudart -lcufft -Xcompiler "/MD /EHsc /O2"
echo NVCC_EXIT=%errorlevel%
