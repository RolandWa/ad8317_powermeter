# ----------------------------------------------
# Script Recorded by Ansys Electronics Desktop Version 2026.1.2
# 20:24:00  Aug 12, 2026
# ----------------------------------------------
import ScriptEnv
ScriptEnv.Initialize("Ansoft.ElectronicsDesktop")
oDesktop.RestoreWindow()
oProject = oDesktop.SetActiveProject("SMA_Coupon_MS")
oDesign = oProject.SetActiveDesign("SMA_Coupon_MS_4L")
oEditor = oDesign.SetActiveEditor("3D Modeler")
oEditor.Rotate(
	[
		"NAME:Selections",
		"Selections:="		, "2023R1_HRMG_300_468B2",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:RotateParameters",
		"RotateAxis:="		, "Y",
		"RotateAngle:="		, "90deg"
	])
oEditor.Rotate(
	[
		"NAME:Selections",
		"Selections:="		, "2023R1_HRMG_300_468B2",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:RotateParameters",
		"RotateAxis:="		, "X",
		"RotateAngle:="		, "90deg"
	])
oEditor.Mirror(
	[
		"NAME:Selections",
		"Selections:="		, "2023R1_HRMG_300_468B2",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:MirrorParameters",
		"MirrorBaseX:="		, "-0.02mm",
		"MirrorBaseY:="		, "60mm",
		"MirrorBaseZ:="		, "0mm",
		"MirrorNormalX:="	, "-1mm",
		"MirrorNormalY:="	, "0mm",
		"MirrorNormalZ:="	, "0mm"
	])
oEditor.Move(
	[
		"NAME:Selections",
		"Selections:="		, "2023R1_HRMG_300_468B2",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:TranslateParameters",
		"TranslateVectorX:="	, "-89.96mm",
		"TranslateVectorY:="	, "-60mm",
		"TranslateVectorZ:="	, "-1.5902mm"
	])
oEditor.Rotate(
	[
		"NAME:Selections",
		"Selections:="		, "2023R1_HRMG_300_468B1",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:RotateParameters",
		"RotateAxis:="		, "X",
		"RotateAngle:="		, "90deg"
	])
oEditor.Rotate(
	[
		"NAME:Selections",
		"Selections:="		, "2023R1_HRMG_300_468B1",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:RotateParameters",
		"RotateAxis:="		, "Z",
		"RotateAngle:="		, "90deg"
	])
oEditor.Move(
	[
		"NAME:Selections",
		"Selections:="		, "2023R1_HRMG_300_468B1",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:TranslateParameters",
		"TranslateVectorX:="	, "-28.4273mm",
		"TranslateVectorY:="	, "30mm",
		"TranslateVectorZ:="	, "-1.5902mm"
	])
oEditor = oDesign.SetActiveEditor("3D Modeler")
oEditor.Move(
	[
		"NAME:Selections",
		"Selections:="		, "2023R1_HRMG_300_468B1",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:TranslateParameters",
		"TranslateVectorX:="	, "20mm",
		"TranslateVectorY:="	, "-20mm",
		"TranslateVectorZ:="	, "1.11022302462516e-15mm"
	])
oEditor.Move(
	[
		"NAME:Selections",
		"Selections:="		, "2023R1_HRMG_300_468B2",
		"NewPartsModelFlag:="	, "Model"
	], 
	[
		"NAME:TranslateParameters",
		"TranslateVectorX:="	, "60mm",
		"TranslateVectorY:="	, "40mm",
		"TranslateVectorZ:="	, "1.99840144432528e-15mm"
	])
