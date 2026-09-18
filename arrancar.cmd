@echo off
rem Atalho de Windows para o arrancar.py, sem depender da politica de
rem execucao do PowerShell. Ver a docstring desse ficheiro.
where py >nul 2>nul
if errorlevel 1 goto sem_launcher
py -3 "%~dp0arrancar.py" %*
goto fim
:sem_launcher
python "%~dp0arrancar.py" %*
:fim
